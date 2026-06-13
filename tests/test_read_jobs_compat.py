"""Tests for _read_jobs tolerant deserialization (legacy / malformed records).

Regression coverage for the live bug where projects with jobs.json written
before asset_type was added crash the whole workspace with a ValidationError.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

import pytest

from core.generation.service import GenerationService
from core.models.schemas import (
    GenerationJob,
    GenerationJobStatus,
    Modality,
    ProjectCreateRequest,
)
from core.project.manager import ProjectManager


# ---------------------------------------------------------------------------
# Minimal fakes (identical to test_batch_execute.py pattern)
# ---------------------------------------------------------------------------


class _FakeWorker:
    is_installed = True
    is_running = True
    display_name = "ComfyUI"
    readiness_note = None
    path = "/tmp/comfyui"
    health_check = "http://127.0.0.1:8188/system_stats"


class _FakeWorkers:
    def get_worker(self, name: str) -> _FakeWorker:
        return _FakeWorker()

    def readiness_note(self, name: str) -> None:
        return None

    def mark_worker_activity(self, name: str, active: bool) -> None:
        return None


def _now() -> datetime:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _raw_job(**overrides) -> dict:
    """Return a minimal well-formed raw job dict (as stored in jobs.json)."""
    base: dict = {
        "id": "job-001",
        "project_id": "proj-001",
        "title": "Test job",
        "modality": "image",
        "asset_type": "image",
        "status": "planned",
        "prompt": "a knight",
        "summary": "a knight summary",
        "created_at": _now(),
        "updated_at": _now(),
    }
    base.update(overrides)
    return base


def _write_raw_jobs(project_dir: Path, raw_items: list[dict]) -> None:
    """Write a jobs.json directly from raw dicts (bypasses model validation)."""
    jobs_path = project_dir / "jobs.json"
    jobs_path.write_text(
        json.dumps({"jobs": raw_items}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


@pytest.fixture()
def svc_and_project(tmp_path: Path):
    manager = ProjectManager(tmp_path / "projects")
    project = manager.create_project(ProjectCreateRequest(name="Demo", type="RPG", synopsis="compat test"))
    service = GenerationService(manager, _FakeWorkers())
    _, project_dir = manager.get_project(project.id)
    return service, project.id, project_dir


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_legacy_job_missing_asset_type_is_loaded(svc_and_project) -> None:
    """A legacy job dict WITHOUT asset_type but WITH modality loads without raising,
    and the resulting job has asset_type derived from modality."""
    service, project_id, project_dir = svc_and_project

    legacy = _raw_job()
    del legacy["asset_type"]  # simulate pre-asset_type schema
    assert "asset_type" not in legacy
    assert legacy["modality"] == "image"

    _write_raw_jobs(project_dir, [legacy])

    workspace = service.list_workspace(project_id)
    assert len(workspace.jobs) == 1
    job = workspace.jobs[0]
    assert job.id == "job-001"
    assert job.asset_type == "image"  # derived from modality


def test_legacy_job_modality_maps_to_asset_type(svc_and_project) -> None:
    """Derive asset_type from each supported modality value."""
    service, project_id, project_dir = svc_and_project

    modalities = ["image", "text", "music", "voice", "video", "training"]
    raw_items = []
    for i, mod in enumerate(modalities):
        item = _raw_job(id=f"job-{i:03d}", modality=mod)
        del item["asset_type"]
        raw_items.append(item)

    _write_raw_jobs(project_dir, raw_items)

    workspace = service.list_workspace(project_id)
    assert len(workspace.jobs) == len(modalities)
    for job in workspace.jobs:
        assert job.asset_type == job.modality.value


def test_irreparably_malformed_job_is_skipped(svc_and_project) -> None:
    """A record missing required fields beyond what can be patched is skipped,
    not raised. One bad record must not prevent loading valid ones."""
    service, project_id, project_dir = svc_and_project

    valid = _raw_job(id="good-001")
    malformed = {"id": "bad-001", "garbage": True}  # missing nearly everything

    _write_raw_jobs(project_dir, [valid, malformed])

    workspace = service.list_workspace(project_id)
    assert len(workspace.jobs) == 1
    assert workspace.jobs[0].id == "good-001"


def test_irreparably_malformed_job_logs_warning(svc_and_project, caplog) -> None:
    """Skipped malformed records emit a warning (no silent drop)."""
    service, project_id, project_dir = svc_and_project

    malformed = {"id": "bad-002", "garbage": True}
    _write_raw_jobs(project_dir, [malformed])

    with caplog.at_level(logging.WARNING, logger="misaka.generation.service"):
        service.list_workspace(project_id)

    assert any("skipped" in msg.lower() for msg in caplog.messages)


def test_legacy_upgrade_logs_warning(svc_and_project, caplog) -> None:
    """Legacy job missing asset_type emits a warning noting the upgrade."""
    service, project_id, project_dir = svc_and_project

    legacy = _raw_job(id="legacy-001")
    del legacy["asset_type"]
    _write_raw_jobs(project_dir, [legacy])

    with caplog.at_level(logging.WARNING, logger="misaka.generation.service"):
        service.list_workspace(project_id)

    assert any("legacy job upgraded" in msg for msg in caplog.messages)


def test_well_formed_jobs_unchanged(svc_and_project) -> None:
    """Well-formed jobs deserialize exactly as before; no field is altered."""
    service, project_id, project_dir = svc_and_project

    job_a = _raw_job(id="a-001", modality="image", asset_type="image", title="Alpha")
    job_b = _raw_job(id="b-001", modality="music", asset_type="music", title="Beta")
    _write_raw_jobs(project_dir, [job_a, job_b])

    workspace = service.list_workspace(project_id)
    assert len(workspace.jobs) == 2
    ids = {j.id for j in workspace.jobs}
    assert ids == {"a-001", "b-001"}
    alpha = next(j for j in workspace.jobs if j.id == "a-001")
    assert alpha.title == "Alpha"
    assert alpha.asset_type == "image"
    assert alpha.modality is Modality.IMAGE
