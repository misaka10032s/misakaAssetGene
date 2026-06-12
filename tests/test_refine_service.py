"""Tests for GenerationService.refine_asset: job creation from the §6.2
decision tree and parent-child lineage recording on produced assets (§5.11)."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from core.generation.adapters.common import AdapterExecutionResult, GeneratedArtifact
from core.generation.service import GenerationService
from core.models.schemas import (
    GenerationJobStatus,
    GenerationRecipe,
    Modality,
    ProjectCreateRequest,
    RefineRequest,
    RefineStrategy,
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


def _seed_image_asset(service: GenerationService, project_id: str) -> str:
    workspace = service.import_asset(
        project_id,
        filename="base.png",
        content=b"PNG",
        modality=Modality.IMAGE,
        asset_type="image",
        title="Base portrait",
    )
    return workspace.assets[-1].id


def test_refine_creates_img2img_job_with_lineage(service) -> None:
    svc, _, project_id = service
    parent_id = _seed_image_asset(svc, project_id)

    workspace = svc.refine_asset(
        project_id,
        parent_id,
        RefineRequest(instruction="讓氛圍更溫暖一點，手再抬高一點"),
    )
    job = next(j for j in workspace.jobs if j.parent_asset_id == parent_id)
    assert job.recipe is GenerationRecipe.IMG2IMG
    assert job.refine_strategy is RefineStrategy.IMG2IMG
    assert job.source_asset_id == parent_id
    assert job.worker == "comfyui"
    assert "denoise" in job.params
    assert job.prompt_delta == "讓氛圍更溫暖一點，手再抬高一點"
    assert job.refine_reason


def test_refine_inpaint_carries_mask(service) -> None:
    svc, _, project_id = service
    parent_id = _seed_image_asset(svc, project_id)
    # A second asset acts as the mask.
    mask_ws = svc.import_asset(
        project_id, filename="mask.png", content=b"M", modality=Modality.IMAGE, asset_type="mask", title="Mask"
    )
    mask_id = mask_ws.assets[-1].id

    workspace = svc.refine_asset(
        project_id,
        parent_id,
        RefineRequest(instruction="只把帽子改成紅色", mask_asset_id=mask_id),
    )
    job = next(j for j in workspace.jobs if j.parent_asset_id == parent_id)
    assert job.refine_strategy is RefineStrategy.INPAINT
    assert job.recipe is GenerationRecipe.INPAINT
    assert job.mask_asset_id == mask_id


def test_metadata_only_refine_completes_without_worker(service) -> None:
    svc, _, project_id = service
    parent_id = _seed_image_asset(svc, project_id)

    workspace = svc.refine_asset(
        project_id,
        parent_id,
        RefineRequest(instruction="把這張標記為最愛並加上 #warm 標籤"),
    )
    job = next(j for j in workspace.jobs if j.parent_asset_id == parent_id)
    # Metadata-only edits do not run a worker; they complete immediately.
    assert job.refine_strategy is RefineStrategy.METADATA_ONLY
    assert job.status is GenerationJobStatus.COMPLETED


def test_executed_refine_records_parent_on_asset(service, monkeypatch) -> None:
    svc, _, project_id = service
    parent_id = _seed_image_asset(svc, project_id)

    workspace = svc.refine_asset(
        project_id,
        parent_id,
        RefineRequest(instruction="氛圍更溫暖一點", strategy=RefineStrategy.IMG2IMG),
    )
    job = next(j for j in workspace.jobs if j.parent_asset_id == parent_id)

    def fake_run(self, project_dir, job_arg, report_progress):
        return AdapterExecutionResult(
            artifacts=[
                GeneratedArtifact(
                    modality=Modality.IMAGE,
                    asset_type="image",
                    title="Refined",
                    filename="refined.png",
                    content=b"NEW",
                )
            ]
        )

    monkeypatch.setattr(GenerationService, "_run_job_with_progress", fake_run)
    result = svc.execute_job(project_id, job.id)
    refined = next(a for a in result.assets if a.title == "Refined")
    assert refined.parent_version_id == parent_id
    assert refined.refine_strategy is RefineStrategy.IMG2IMG
