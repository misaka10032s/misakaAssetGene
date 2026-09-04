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


def test_fetch_live_checkpoints_parses_object_info(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {
        "CheckpointLoaderSimple": {
            "input": {"required": {"ckpt_name": [["b.ckpt", "a.ckpt"], {"tooltip": "x"}]}}
        }
    }
    monkeypatch.setattr(comfyui.httpx, "get", lambda url, **k: _resp(url, "GET", json=payload))
    # Sorted deterministically regardless of the server's enum order.
    assert comfyui.fetch_live_checkpoints("http://x") == ["a.ckpt", "b.ckpt"]


def test_fetch_live_checkpoints_returns_empty_on_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(url: str, **k):
        raise httpx.ConnectError("refused", request=httpx.Request("GET", url))

    monkeypatch.setattr(comfyui.httpx, "get", _boom)
    assert comfyui.fetch_live_checkpoints("http://x") == []


def test_resolve_checkpoint_prefers_live(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # No local checkpoints -- a standalone ComfyUI with its own model dirs must
    # still resolve a checkpoint from the live server (spec §5.13 live-first).
    monkeypatch.setattr(comfyui, "fetch_live_checkpoints", lambda base_url, **k: ["x.ckpt", "y.ckpt"])
    assert comfyui._resolve_checkpoint_name(tmp_path / "worker", base_url="http://x") == "x.ckpt"


def test_resolve_checkpoint_falls_back_to_local(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ckpt_dir = tmp_path / "worker" / "models" / "checkpoints"
    ckpt_dir.mkdir(parents=True)
    (ckpt_dir / "local.safetensors").write_bytes(b"x")
    monkeypatch.setattr(comfyui, "fetch_live_checkpoints", lambda base_url, **k: [])
    assert comfyui._resolve_checkpoint_name(tmp_path / "worker", base_url="http://x") == "local.safetensors"


def test_resolve_checkpoint_honours_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(comfyui, "fetch_live_checkpoints", lambda base_url, **k: ["a.ckpt", "b.ckpt"])
    assert comfyui._resolve_checkpoint_name(tmp_path / "worker", base_url="http://x", override="b.ckpt") == "b.ckpt"


def test_resolve_checkpoint_override_beats_default_beats_live_first(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Resolution order (spec §6.2 / BP-COMFY): explicit override > configured
    default > live[0]."""
    monkeypatch.setattr(
        comfyui, "fetch_live_checkpoints", lambda base_url, **k: ["a.ckpt", "b.ckpt", "c.ckpt"]
    )
    # override wins even though both default and live[0] are also present.
    assert (
        comfyui._resolve_checkpoint_name(
            tmp_path / "worker", base_url="http://x", override="c.ckpt", default="b.ckpt"
        )
        == "c.ckpt"
    )
    # no override -> configured default wins over live[0] ("a.ckpt").
    assert (
        comfyui._resolve_checkpoint_name(
            tmp_path / "worker", base_url="http://x", default="b.ckpt"
        )
        == "b.ckpt"
    )
    # neither override nor default -> falls back to live[0].
    assert (
        comfyui._resolve_checkpoint_name(tmp_path / "worker", base_url="http://x") == "a.ckpt"
    )


def test_resolve_checkpoint_default_absent_from_live_falls_through_with_warning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A configured default that the live server does not advertise must NOT
    raise -- it logs a warning and falls through to live[0] (spec §6.2)."""
    monkeypatch.setattr(comfyui, "fetch_live_checkpoints", lambda base_url, **k: ["a.ckpt", "b.ckpt"])
    with caplog.at_level("WARNING", logger="misaka.generation.adapters.comfyui"):
        result = comfyui._resolve_checkpoint_name(
            tmp_path / "worker", base_url="http://x", default="missing.ckpt"
        )
    assert result == "a.ckpt"
    assert any("missing.ckpt" in record.getMessage() for record in caplog.records)


def test_execute_end_to_end_with_mocked_api(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    checkpoint_dir = tmp_path / "worker" / "models" / "checkpoints"
    checkpoint_dir.mkdir(parents=True)
    (checkpoint_dir / "model.safetensors").write_bytes(b"ckpt")
    # Force the local fallback path so the test makes no real network call.
    monkeypatch.setattr(comfyui, "fetch_live_checkpoints", lambda base_url, **k: [])

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
