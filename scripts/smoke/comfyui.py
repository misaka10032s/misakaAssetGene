"""ComfyUI smoke test (spec §6.1 / §5.11).

Two modes:

1. Health mode (default): verify a checkpoint is installed and the
   ``/system_stats`` endpoint answers. Driven by the worker manager via the
   ``MISAKA_WORKER_*`` env vars; prints a single JSON line and exits 0/1.

2. Generation mode (opt-in, real e2e): when ``MISAKA_SMOKE_MODE`` is one of
   ``txt2img`` / ``img2img`` / ``inpaint`` / ``all``, submit a real workflow to
   a running ComfyUI server and download the produced image. This exercises the
   exact workflow graphs the adapter builds. It needs a real checkpoint on the
   server, so it is never run by the automatic worker smoke path.

Generation-mode env:
    MISAKA_SMOKE_BASE_URL   default http://127.0.0.1:8188
    MISAKA_SMOKE_CHECKPOINT optional explicit checkpoint name (else auto-pick)
    MISAKA_SMOKE_OUT_DIR    default ./.cache/smoke/comfyui
"""

from __future__ import annotations

import json
import os
import sys
import uuid
from pathlib import Path

import httpx

# Allow running as a standalone script from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core.config import get_settings  # noqa: E402
from core.generation.adapters import comfyui  # noqa: E402
from core.models.schemas import GenerationRecipe  # noqa: E402


def _health_main() -> int:
    health_url = os.environ.get("MISAKA_WORKER_HEALTH_URL", "").strip()
    checkpoint_dir = Path(os.environ.get("MISAKA_WORKER_CHECKPOINT_DIR", ""))
    if checkpoint_dir.exists():
        checkpoints = [item for item in checkpoint_dir.iterdir() if item.is_file() and item.name != "put_checkpoints_here"]
        if not checkpoints:
            print(json.dumps({"ok": False, "detail": "No ComfyUI checkpoint is installed."}, ensure_ascii=False))
            return 1
    try:
        response = httpx.get(health_url, timeout=5.0)
        response.raise_for_status()
    except httpx.HTTPError as error:
        print(json.dumps({"ok": False, "detail": f"ComfyUI health check failed: {error}"}, ensure_ascii=False))
        return 1
    print(json.dumps({"ok": True, "detail": "ComfyUI health check succeeded."}, ensure_ascii=False))
    return 0


def _pick_checkpoint(client: httpx.Client, base_url: str) -> str:
    explicit = os.environ.get("MISAKA_SMOKE_CHECKPOINT", "").strip()
    if explicit:
        return explicit
    info = client.get(f"{base_url}/object_info/CheckpointLoaderSimple", timeout=30.0).json()
    names = info["CheckpointLoaderSimple"]["input"]["required"]["ckpt_name"][0]
    if not names:
        raise RuntimeError("ComfyUI server reports no installed checkpoints.")
    return names[0]


def _make_solid_png(width: int, height: int, color: tuple[int, int, int]) -> bytes:
    from PIL import Image
    import io

    buffer = io.BytesIO()
    Image.new("RGB", (width, height), color).save(buffer, format="PNG")
    return buffer.getvalue()


def _make_mask_png(width: int, height: int) -> bytes:
    """A center white square on a black background (white = inpaint region)."""
    from PIL import Image
    import io

    image = Image.new("L", (width, height), 0)
    box = (width // 4, height // 4, width * 3 // 4, height * 3 // 4)
    for x in range(box[0], box[2]):
        for y in range(box[1], box[3]):
            image.putpixel((x, y), 255)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _run_recipe(
    client: httpx.Client,
    base_url: str,
    checkpoint: str,
    recipe: GenerationRecipe,
    out_dir: Path,
) -> dict:
    prompt_id = uuid.uuid4().hex
    filename_prefix = f"smoke-{recipe.value}-{prompt_id[:8]}"
    source_path = None
    mask_path = None

    if recipe in (GenerationRecipe.IMG2IMG, GenerationRecipe.INPAINT):
        source_path = out_dir / f"{filename_prefix}-source.png"
        source_path.write_bytes(_make_solid_png(768, 768, (90, 130, 200)))
    if recipe is GenerationRecipe.INPAINT:
        mask_path = out_dir / f"{filename_prefix}-mask.png"
        mask_path.write_bytes(_make_mask_png(768, 768))

    workflow = comfyui._build_workflow(
        recipe=recipe,
        client=client,
        base_url=base_url,
        checkpoint_name=checkpoint,
        positive_prompt="a simple anime character portrait, soft lighting",
        negative_prompt=get_settings().misaka_comfyui_default_negative_prompt,
        filename_prefix=filename_prefix,
        seed=12345,
        params={"steps": 12, "width": 768, "height": 768},
        source_asset_path=source_path,
        mask_asset_path=mask_path,
    )
    response = client.post(
        f"{base_url}/prompt",
        json={"prompt": workflow, "client_id": "misaka-smoke", "prompt_id": prompt_id},
    )
    response.raise_for_status()

    class _Ctx:
        report_progress = None

    history = comfyui._wait_for_history(client, base_url, prompt_id, _Ctx())
    images = comfyui._download_output_images(client, base_url, history)
    if not images:
        raise RuntimeError(f"{recipe.value}: ComfyUI returned no images.")

    saved: list[str] = []
    for index, image in enumerate(images, start=1):
        target = out_dir / f"{filename_prefix}-out{index}.png"
        target.write_bytes(image["content"])
        saved.append(str(target))
    return {"recipe": recipe.value, "prompt_id": prompt_id, "outputs": saved}


def _generation_main(mode: str) -> int:
    base_url = os.environ.get("MISAKA_SMOKE_BASE_URL", "http://127.0.0.1:8188").rstrip("/")
    out_dir = Path(os.environ.get("MISAKA_SMOKE_OUT_DIR", "")) or Path(".cache") / "smoke" / "comfyui"
    out_dir.mkdir(parents=True, exist_ok=True)

    recipes = {
        "txt2img": [GenerationRecipe.TXT2IMG],
        "img2img": [GenerationRecipe.IMG2IMG],
        "inpaint": [GenerationRecipe.INPAINT],
        "all": [GenerationRecipe.TXT2IMG, GenerationRecipe.IMG2IMG, GenerationRecipe.INPAINT],
    }[mode]

    results: list[dict] = []
    with httpx.Client(timeout=300.0) as client:
        checkpoint = _pick_checkpoint(client, base_url)
        for recipe in recipes:
            results.append(_run_recipe(client, base_url, checkpoint, recipe, out_dir))

    print(json.dumps({"ok": True, "checkpoint": checkpoint, "results": results}, ensure_ascii=False, indent=2))
    return 0


def main() -> int:
    mode = os.environ.get("MISAKA_SMOKE_MODE", "").strip().lower()
    if mode in ("txt2img", "img2img", "inpaint", "all"):
        try:
            return _generation_main(mode)
        except Exception as error:  # noqa: BLE001 - smoke test surfaces any failure
            print(json.dumps({"ok": False, "detail": f"{type(error).__name__}: {error}"}, ensure_ascii=False))
            return 1
    return _health_main()


if __name__ == "__main__":
    raise SystemExit(main())
