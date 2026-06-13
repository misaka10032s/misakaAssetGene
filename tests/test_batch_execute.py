"""Tests for execute_ready_jobs batch-skip honesty (spec §5.14).

Verifies that:
- Blocked jobs in the request set are collected into BatchExecuteData.skipped
  rather than silently ignored.
- executed_count reflects only actually-run jobs.
- READY / PLANNED jobs that can execute do so and are NOT in the skipped list.
- Jobs not in the requested set are neither executed nor skipped.
- The SkippedJobInfo carries job_id, title, and the blocking_reason.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from core.generation.service import GenerationService
from core.models.schemas import (
    BatchExecuteData,
    GenerationJob,
    GenerationJobStatus,
    Modality,
    ProjectCreateRequest,
    SkippedJobInfo,
)
from core.project.manager import ProjectManager


# ---------------------------------------------------------------------------
# Minimal fakes
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
    return datetime.now(timezone.utc)


def _make_job(**kwargs) -> GenerationJob:
    defaults = dict(
        id="job-001",
        project_id="proj-001",
        title="Test job",
        modality=Modality.IMAGE,
        asset_type="image",
        status=GenerationJobStatus.READY,
        prompt="test prompt",
        summary="test summary",
        worker="comfyui",
        blocking_reason=None,
        created_at=_now(),
        updated_at=_now(),
    )
    defaults.update(kwargs)
    return GenerationJob(**defaults)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def svc_and_project(tmp_path: Path):
    manager = ProjectManager(tmp_path / "projects")
    project = manager.create_project(ProjectCreateRequest(name="Demo", type="RPG", synopsis="batch test"))
    service = GenerationService(manager, _FakeWorkers())
    return service, project.id


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def _write_jobs(service: GenerationService, project_id: str, jobs: list[GenerationJob]) -> None:
    """Bypass the normal create path and write arbitrary jobs directly."""
    import json

    _, project_dir = service.project_manager.get_project(project_id)
    jobs_path = project_dir / "jobs.json"
    jobs_path.write_text(
        json.dumps({"jobs": [j.model_dump(mode="json") for j in jobs]}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def test_execute_ready_returns_batch_execute_data(svc_and_project) -> None:
    """Return type is BatchExecuteData, not plain ProjectWorkspaceData."""
    service, project_id = svc_and_project
    result = service.execute_ready_jobs(project_id)
    assert isinstance(result, BatchExecuteData)


def test_no_jobs_gives_zero_counts(svc_and_project) -> None:
    service, project_id = svc_and_project
    result = service.execute_ready_jobs(project_id)
    assert result.executed_count == 0
    assert result.skipped == []


def test_blocked_job_in_requested_set_is_skipped(svc_and_project) -> None:
    """A BLOCKED job that is explicitly requested goes to skipped, not executed."""
    service, project_id = svc_and_project
    blocked = _make_job(
        id="blocked-001",
        status=GenerationJobStatus.BLOCKED,
        blocking_reason="Worker not running",
        title="Blocked image",
    )
    _write_jobs(service, project_id, [blocked])

    result = service.execute_ready_jobs(project_id, job_ids=["blocked-001"])

    assert result.executed_count == 0
    assert len(result.skipped) == 1
    skip = result.skipped[0]
    assert isinstance(skip, SkippedJobInfo)
    assert skip.job_id == "blocked-001"
    assert skip.title == "Blocked image"
    assert skip.reason == "Worker not running"


def test_blocked_job_without_reason_uses_fallback(svc_and_project) -> None:
    """A BLOCKED job with no blocking_reason still produces a non-empty reason."""
    service, project_id = svc_and_project
    blocked = _make_job(
        id="blocked-002",
        status=GenerationJobStatus.BLOCKED,
        blocking_reason=None,
    )
    _write_jobs(service, project_id, [blocked])

    result = service.execute_ready_jobs(project_id, job_ids=["blocked-002"])

    assert len(result.skipped) == 1
    assert result.skipped[0].reason  # non-empty fallback


def test_ready_job_is_executed_and_not_skipped(svc_and_project) -> None:
    """A READY job that runs successfully increments executed_count and is not skipped."""
    service, project_id = svc_and_project
    ready = _make_job(id="ready-001", status=GenerationJobStatus.READY)
    _write_jobs(service, project_id, [ready])

    fake_result = type(
        "_FakeResult",
        (),
        {
            "artifacts": [],
            "progress_updates": [],
            "last_error": None,
        },
    )()

    with patch("core.generation.service.get_adapter") as mock_get:
        mock_adapter = mock_get.return_value
        mock_adapter.execute.return_value = fake_result

        result = service.execute_ready_jobs(project_id)

    assert result.executed_count == 1
    assert result.skipped == []


def test_mixed_ready_and_blocked_jobs(svc_and_project) -> None:
    """Two jobs: one READY, one BLOCKED.  executed=1, skipped=1."""
    service, project_id = svc_and_project
    ready = _make_job(id="ready-001", status=GenerationJobStatus.READY)
    blocked = _make_job(
        id="blocked-001",
        status=GenerationJobStatus.BLOCKED,
        blocking_reason="No checkpoint",
        title="Blocked job",
    )
    _write_jobs(service, project_id, [ready, blocked])

    fake_result = type(
        "_FakeResult",
        (),
        {
            "artifacts": [],
            "progress_updates": [],
            "last_error": None,
        },
    )()

    with patch("core.generation.service.get_adapter") as mock_get:
        mock_adapter = mock_get.return_value
        mock_adapter.execute.return_value = fake_result

        result = service.execute_ready_jobs(project_id, job_ids=["ready-001", "blocked-001"])

    assert result.executed_count == 1
    assert len(result.skipped) == 1
    assert result.skipped[0].job_id == "blocked-001"


def test_blocked_job_not_in_requested_set_is_neither_executed_nor_skipped(svc_and_project) -> None:
    """A BLOCKED job not in the requested set is invisible to the caller."""
    service, project_id = svc_and_project
    ready = _make_job(id="ready-001", status=GenerationJobStatus.READY)
    blocked_other = _make_job(
        id="blocked-999",
        status=GenerationJobStatus.BLOCKED,
        blocking_reason="irrelevant",
    )
    _write_jobs(service, project_id, [ready, blocked_other])

    fake_result = type(
        "_FakeResult",
        (),
        {
            "artifacts": [],
            "progress_updates": [],
            "last_error": None,
        },
    )()

    with patch("core.generation.service.get_adapter") as mock_get:
        mock_adapter = mock_get.return_value
        mock_adapter.execute.return_value = fake_result

        result = service.execute_ready_jobs(project_id, job_ids=["ready-001"])

    assert result.executed_count == 1
    assert result.skipped == []


def test_workspace_is_nested_inside_batch_execute_data(svc_and_project) -> None:
    """The workspace is accessible at result.workspace (not the root object itself)."""
    service, project_id = svc_and_project
    result = service.execute_ready_jobs(project_id)
    # workspace must carry jobs/assets/plans attributes
    assert hasattr(result.workspace, "jobs")
    assert hasattr(result.workspace, "assets")
    assert hasattr(result.workspace, "plans")
