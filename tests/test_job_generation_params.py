"""Tests for normal-job generation params (spec §6.2 / BP-COMFY): a job can be
patched with tunable params (checkpoint/steps/cfg/width/height/sampler/
scheduler), newly-built IMAGE jobs are seeded with the configured default
checkpoint, and the ComfyUI adapter honours the resolution order
override > configured default > live[0] all the way to the submitted
workflow's ``CheckpointLoaderSimple.ckpt_name``.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from core.generation import service as service_module
from core.generation.adapters import comfyui
from core.generation.adapters.common import AdapterContext
from core.generation.service import GenerationService
from core.models.schemas import (
    ClarifyResult,
    ConsultantAnalysis,
    ConsultantDeliverable,
    GenerationRecipe,
    JobExecutionPatch,
    Modality,
    ProjectCreateRequest,
)
from core.project.manager import ProjectManager


class _FakeWorker:
    is_installed = True
    is_running = True
    display_name = "ComfyUI"
    readiness_note = None
    path = "/tmp/comfyui"
    health_check = "http://127.0.0.1:8188/system_stats"


class _FakeWorkers:
    def get_worker(self, name):
        return _FakeWorker()

    def readiness_note(self, name):
        return None

    def mark_worker_activity(self, name, active):
        return None


@pytest.fixture()
def service(tmp_path: Path):
    manager = ProjectManager(tmp_path / "projects")
    project = manager.create_project(ProjectCreateRequest(name="Demo", type="RPG", synopsis="test"))
    return GenerationService(manager, _FakeWorkers()), manager, project.id


def _clarify_result_with_image_deliverable() -> ClarifyResult:
    return ClarifyResult(
        modality=Modality.IMAGE,
        summary="A hero portrait plan",
        template_loaded=True,
        next_step="generate",
        analysis=ConsultantAnalysis(
            objective="hero portrait",
            deliverables=[
                ConsultantDeliverable(
                    modality=Modality.IMAGE,
                    asset_type="image",
                    title="Hero portrait",
                    worker="comfyui",
                )
            ],
        ),
    )


# ---------------------------------------------------------------------------
# (a) newly-built IMAGE jobs are seeded with the configured default checkpoint
# ---------------------------------------------------------------------------


def test_build_job_seeds_configured_default_checkpoint(service, monkeypatch: pytest.MonkeyPatch) -> None:
    svc, _, project_id = service

    class _FakeSettings:
        misaka_comfyui_default_checkpoint = "novaAnimeXL_ilV180.safetensors"

    monkeypatch.setattr(service_module, "get_settings", lambda: _FakeSettings())

    workspace = svc.record_plan(project_id, "a brave knight", _clarify_result_with_image_deliverable())
    job = next(j for j in workspace.jobs if j.modality is Modality.IMAGE)
    assert job.params.get("checkpoint") == "novaAnimeXL_ilV180.safetensors"


def test_build_job_default_checkpoint_reads_from_real_settings(service) -> None:
    """Without any monkeypatch, the real Settings default
    (novaAnimeXL_ilV180.safetensors, per user directive 2026-09-04) is what
    gets seeded -- confirms config.py wiring, not just the test double."""
    svc, _, project_id = service
    workspace = svc.record_plan(project_id, "a brave knight", _clarify_result_with_image_deliverable())
    job = next(j for j in workspace.jobs if j.modality is Modality.IMAGE)
    assert job.params.get("checkpoint") == "novaAnimeXL_ilV180.safetensors"


# ---------------------------------------------------------------------------
# (b) PATCH/update_job persists params, merging (not replacing) existing keys
# ---------------------------------------------------------------------------


def test_update_job_persists_checkpoint_param(service) -> None:
    svc, _, project_id = service
    workspace = svc.record_plan(project_id, "a brave knight", _clarify_result_with_image_deliverable())
    job = next(j for j in workspace.jobs if j.modality is Modality.IMAGE)

    updated = svc.update_job(project_id, job.id, JobExecutionPatch(params={"checkpoint": "myAnimeCkpt.safetensors"}))
    updated_job = next(j for j in updated.jobs if j.id == job.id)
    assert updated_job.params["checkpoint"] == "myAnimeCkpt.safetensors"


def test_update_job_params_merges_without_dropping_existing_keys(service) -> None:
    svc, _, project_id = service
    workspace = svc.record_plan(project_id, "a brave knight", _clarify_result_with_image_deliverable())
    job = next(j for j in workspace.jobs if j.modality is Modality.IMAGE)
    assert "checkpoint" in job.params  # seeded default from _build_job

    updated = svc.update_job(project_id, job.id, JobExecutionPatch(params={"steps": 30, "cfg": 6}))
    updated_job = next(j for j in updated.jobs if j.id == job.id)
    assert updated_job.params["steps"] == 30
    assert updated_job.params["cfg"] == 6
    # The default checkpoint seeded at build time must survive an unrelated patch.
    assert updated_job.params["checkpoint"] == job.params["checkpoint"]


def test_update_job_without_params_leaves_existing_params_untouched(service) -> None:
    svc, _, project_id = service
    workspace = svc.record_plan(project_id, "a brave knight", _clarify_result_with_image_deliverable())
    job = next(j for j in workspace.jobs if j.modality is Modality.IMAGE)

    updated = svc.update_job(project_id, job.id, JobExecutionPatch(worker="comfyui"))
    updated_job = next(j for j in updated.jobs if j.id == job.id)
    assert updated_job.params == job.params


def test_update_job_omitted_recipe_keeps_current_recipe(service) -> None:
    """A params-only PATCH (the normal case) must not null out job.recipe --
    JobExecutionPatch.recipe defaults to None when omitted, and update_job
    must fall back to the job's existing recipe rather than overwriting it
    with that None (reviewer finding, MAJOR)."""
    svc, _, project_id = service
    workspace = svc.record_plan(project_id, "a brave knight", _clarify_result_with_image_deliverable())
    job = next(j for j in workspace.jobs if j.modality is Modality.IMAGE)
    assert job.recipe is not None  # seeded GenerationRecipe.AUTO by _build_job

    updated = svc.update_job(project_id, job.id, JobExecutionPatch(params={"steps": 30}))
    updated_job = next(j for j in updated.jobs if j.id == job.id)
    assert updated_job.recipe == job.recipe


def test_update_job_explicit_recipe_replaces_current_recipe(service) -> None:
    """An explicit recipe in the PATCH body still replaces the current one."""
    svc, _, project_id = service
    workspace = svc.record_plan(project_id, "a brave knight", _clarify_result_with_image_deliverable())
    job = next(j for j in workspace.jobs if j.modality is Modality.IMAGE)
    assert job.recipe != GenerationRecipe.INPAINT

    updated = svc.update_job(project_id, job.id, JobExecutionPatch(recipe=GenerationRecipe.INPAINT))
    updated_job = next(j for j in updated.jobs if j.id == job.id)
    assert updated_job.recipe == GenerationRecipe.INPAINT


# ---------------------------------------------------------------------------
# (c) end-to-end: a patched checkpoint reaches CheckpointLoaderSimple.ckpt_name
# ---------------------------------------------------------------------------


def _resp(url: str, method: str, **kwargs) -> httpx.Response:
    return httpx.Response(200, request=httpx.Request(method, url), **kwargs)


class _FakeClient:
    def __init__(self) -> None:
        self.submitted_workflow: dict | None = None

    def post(self, url: str, **kwargs):
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


def test_patched_checkpoint_reaches_workflow_ckpt_name(service, monkeypatch: pytest.MonkeyPatch) -> None:
    """A job PATCHed with params.checkpoint must reach the submitted ComfyUI
    workflow's CheckpointLoaderSimple.ckpt_name -- the live checkpoint list is
    faked so no real network call happens."""
    svc, _, project_id = service
    workspace = svc.record_plan(project_id, "a brave knight", _clarify_result_with_image_deliverable())
    job = next(j for j in workspace.jobs if j.modality is Modality.IMAGE)

    updated = svc.update_job(
        project_id, job.id, JobExecutionPatch(params={"checkpoint": "userChosen.safetensors"})
    )
    patched_job = next(j for j in updated.jobs if j.id == job.id)

    monkeypatch.setattr(
        comfyui,
        "fetch_live_checkpoints",
        lambda base_url, **k: ["userChosen.safetensors", "novaAnimeXL_ilV180.safetensors"],
    )
    fake = _FakeClient()

    class _CM:
        def __enter__(self):
            return fake

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(comfyui.httpx, "Client", lambda *a, **k: _CM())

    context = AdapterContext(
        project_dir=Path("/tmp/unused-project-dir"),
        job=patched_job,
        worker_path=Path("/tmp/unused-worker"),
        health_check="http://127.0.0.1:8188/system_stats",
    )
    result = comfyui.execute(context)
    assert fake.submitted_workflow is not None
    assert fake.submitted_workflow["4"]["inputs"]["ckpt_name"] == "userChosen.safetensors"
    assert result.metadata["checkpoint"] == "userChosen.safetensors"
