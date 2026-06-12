"""Unit tests for the ComfyUI adapter: workflow graph correctness for
txt2img / img2img / inpaint, param threading, and the HTTP execute path with
the ComfyUI API fully mocked (spec §5.11 / §6.1)."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest

from core.generation.adapters import comfyui
from core.generation.adapters.common import AdapterContext
from core.models.schemas import GenerationJob, GenerationJobStatus, GenerationRecipe, Modality


def _job(recipe: GenerationRecipe, params: dict | None = None) -> GenerationJob:
    now = datetime.now(timezone.utc)
    return GenerationJob(
        id="job1234abcd",
        project_id="proj1",
        title="Hero portrait",
        modality=Modality.IMAGE,
        asset_type="image",
        status=GenerationJobStatus.RUNNING,
        prompt="a brave knight",
        summary="",
        worker="comfyui",
        recipe=recipe,
        params=params or {},
        created_at=now,
        updated_at=now,
    )


def _resp(url: str, method: str, **kwargs) -> httpx.Response:
    """Build an httpx.Response with its request set so raise_for_status works."""
    return httpx.Response(200, request=httpx.Request(method, url), **kwargs)


class _FakeClient:
    """Minimal httpx.Client stand-in capturing the submitted workflow."""

    def __init__(self) -> None:
        self.submitted_workflow: dict | None = None
        self.uploads: list[dict] = []

    def post(self, url: str, **kwargs):
        if url.endswith("/upload/image") or url.endswith("/upload/mask"):
            self.uploads.append({"url": url, "data": kwargs.get("data")})
            return _resp(url, "POST", json={"name": "uploaded.png"})
        if url.endswith("/prompt"):
            self.submitted_workflow = kwargs["json"]["prompt"]
            return _resp(url, "POST", json={"prompt_id": kwargs["json"]["prompt_id"]})
        raise AssertionError(f"unexpected POST {url}")

    def get(self, url: str, **kwargs):
        if "/history/" in url:
            prompt_id = url.rsplit("/", 1)[-1]
            return _resp(
                url,
                "GET",
                json={prompt_id: {"outputs": {"9": {"images": [{"filename": "out.png", "subfolder": "", "type": "output"}]}}}},
            )
        if "/view" in url:
            return _resp(url, "GET", content=b"PNGDATA")
        raise AssertionError(f"unexpected GET {url}")


def test_build_txt2img_workflow_graph() -> None:
    wf = comfyui._build_workflow(
        recipe=GenerationRecipe.TXT2IMG,
        client=None,
        base_url="http://x",
        checkpoint_name="model.safetensors",
        positive_prompt="hero",
        negative_prompt="bad",
        filename_prefix="pfx",
        seed=42,
        params={},
        source_asset_path=None,
        mask_asset_path=None,
    )
    assert wf["4"]["inputs"]["ckpt_name"] == "model.safetensors"
    assert wf["5"]["class_type"] == "EmptyLatentImage"
    assert wf["3"]["inputs"]["denoise"] == 1
    assert wf["3"]["inputs"]["seed"] == 42


def test_img2img_threads_params_and_uploads_source(tmp_path: Path) -> None:
    client = _FakeClient()
    source = tmp_path / "src.png"
    source.write_bytes(b"x")
    wf = comfyui._build_workflow(
        recipe=GenerationRecipe.IMG2IMG,
        client=client,
        base_url="http://x",
        checkpoint_name="m.safetensors",
        positive_prompt="warmer",
        negative_prompt="bad",
        filename_prefix="pfx",
        seed=7,
        params={"denoise": 0.5, "cfg": 6, "steps": 30},
        source_asset_path=source,
        mask_asset_path=None,
    )
    # VAEEncode wires the uploaded source into the latent.
    assert wf["11"]["class_type"] == "VAEEncode"
    assert wf["10"]["class_type"] == "LoadImage"
    assert wf["3"]["inputs"]["denoise"] == 0.5
    assert wf["3"]["inputs"]["cfg"] == 6
    assert wf["3"]["inputs"]["steps"] == 30
    assert client.uploads and client.uploads[0]["data"]["type"] == "input"


def test_inpaint_uses_mask_endpoint_and_conditioning(tmp_path: Path) -> None:
    client = _FakeClient()
    source = tmp_path / "src.png"
    mask = tmp_path / "mask.png"
    source.write_bytes(b"x")
    mask.write_bytes(b"y")
    wf = comfyui._build_workflow(
        recipe=GenerationRecipe.INPAINT,
        client=client,
        base_url="http://x",
        checkpoint_name="m.safetensors",
        positive_prompt="red hat",
        negative_prompt="bad",
        filename_prefix="pfx",
        seed=9,
        params={"denoise": 0.7},
        source_asset_path=source,
        mask_asset_path=mask,
    )
    assert wf["13"]["class_type"] == "InpaintModelConditioning"
    assert wf["11"]["class_type"] == "LoadImageMask"
    assert wf["11"]["inputs"]["channel"] == "red"
    assert wf["3"]["inputs"]["denoise"] == 0.7
    # Both source and mask are uploaded as standalone input images; the mask is
    # then read via LoadImageMask (spec §5.11 inpaint flow). Two uploads total.
    assert len(client.uploads) == 2
    assert all(u["url"].endswith("/upload/image") for u in client.uploads)


def test_img2img_requires_source() -> None:
    with pytest.raises(RuntimeError, match="img2img requires"):
        comfyui._build_workflow(
            recipe=GenerationRecipe.IMG2IMG,
            client=_FakeClient(),
            base_url="http://x",
            checkpoint_name="m",
            positive_prompt="p",
            negative_prompt="n",
            filename_prefix="pfx",
            seed=1,
            params={},
            source_asset_path=None,
            mask_asset_path=None,
        )


def test_inpaint_requires_mask(tmp_path: Path) -> None:
    source = tmp_path / "src.png"
    source.write_bytes(b"x")
    with pytest.raises(RuntimeError, match="inpaint requires"):
        comfyui._build_workflow(
            recipe=GenerationRecipe.INPAINT,
            client=_FakeClient(),
            base_url="http://x",
            checkpoint_name="m",
            positive_prompt="p",
            negative_prompt="n",
            filename_prefix="pfx",
            seed=1,
            params={},
            source_asset_path=source,
            mask_asset_path=None,
        )


def test_execute_end_to_end_with_mocked_api(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    checkpoint_dir = tmp_path / "worker" / "models" / "checkpoints"
    checkpoint_dir.mkdir(parents=True)
    (checkpoint_dir / "model.safetensors").write_bytes(b"ckpt")

    fake = _FakeClient()

    class _CM:
        def __enter__(self):
            return fake

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(comfyui.httpx, "Client", lambda *a, **k: _CM())

    context = AdapterContext(
        project_dir=tmp_path / "proj",
        job=_job(GenerationRecipe.TXT2IMG),
        worker_path=tmp_path / "worker",
        health_check="http://127.0.0.1:8188/system_stats",
    )
    result = comfyui.execute(context)
    assert len(result.artifacts) == 1
    assert result.artifacts[0].content == b"PNGDATA"
    assert result.metadata["checkpoint"] == "model.safetensors"
    assert fake.submitted_workflow is not None
