"""Regression test: M3(a) live-first readiness defeat fix.

Scenario: ComfyUI is running live (readiness_note=None, is_running=True) but
is NOT installed locally (is_installed=False, e.g. standalone ComfyUI process
reached via network with no local clone).  Before the fix, service.py
_build_worker_blocking_reason would fall through to the is_installed check and
return "ComfyUI is not installed yet." even though readiness_note is None.

After the fix, if is_running=True and readiness_note=None the method must
return None (worker is live-usable), and a consultant-planned job must be
created with status READY (not BLOCKED).
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from core.generation.service import GenerationService
from core.models.schemas import (
    ClarifyResult,
    ConsultantAnalysis,
    ConsultantDeliverable,
    GenerationJobStatus,
    Modality,
    ProjectCreateRequest,
    WorkerSnapshot,
)
from core.project.manager import ProjectManager
from core.scheduler.vram import RuntimeState


def _make_live_worker_snapshot() -> WorkerSnapshot:
    """Running standalone ComfyUI: reachable + live, NOT installed locally."""
    return WorkerSnapshot(
        name="comfyui",
        display_name="ComfyUI",
        repo="https://github.com/comfyanonymous/ComfyUI",
        path="/tmp/nonexistent_local_clone",
        recommended_reference="master",
        installed_reference=None,  # NOT installed locally
        health_check="http://127.0.0.1:8188/system_stats",
        is_installed=False,         # NOT installed locally
        is_running=True,            # but LIVE: process is running
        readiness_note=None,        # M3(a): readiness check passes
        runtime_state=RuntimeState.WARM,
    )


class _LiveFakeWorkers:
    """WorkersService stub: worker is live-running but not installed locally."""

    def get_worker(self, name: str) -> WorkerSnapshot:
        return _make_live_worker_snapshot()

    def readiness_note(self, name: str) -> str | None:
        return None  # live-usable: no blocking note

    def mark_worker_activity(self, name: str, active: bool) -> None:
        pass


@pytest.fixture()
def svc(tmp_path: Path):
    manager = ProjectManager(tmp_path / "projects")
    project = manager.create_project(ProjectCreateRequest(name="TestProj", type="RPG", synopsis="test"))
    service = GenerationService(manager, _LiveFakeWorkers())
    return service, project.id


# ---------------------------------------------------------------------------
# Unit test 1: _build_worker_blocking_reason returns None when live
# ---------------------------------------------------------------------------

def test_build_worker_blocking_reason_live_returns_none(svc):
    """Core regression: live worker (is_running=True, readiness_note=None, is_installed=False)
    must yield blocking_reason=None."""
    service, _ = svc
    result = service._build_worker_blocking_reason("comfyui")
    assert result is None, (
        f"Expected None for live worker, got: {result!r}. "
        "is_installed=False fallback must NOT fire when is_running=True."
    )


# ---------------------------------------------------------------------------
# Unit test 2: consultant-planned job is created READY (not BLOCKED)
# ---------------------------------------------------------------------------

def test_planned_job_ready_when_worker_live_not_installed(svc):
    """End-to-end: consultant plan for comfyui worker must produce a READY job
    when worker is live (not blocked by local install state)."""
    service, project_id = svc

    deliverable = ConsultantDeliverable(
        modality=Modality.IMAGE,
        asset_type="image",
        title="角色立繪",
        variants=[],
        worker="comfyui",
    )
    result = ClarifyResult(
        modality=Modality.IMAGE,
        summary="生成角色立繪",
        questions=[],
        template_loaded=True,
        next_step="generate",
        analysis=ConsultantAnalysis(
            objective="generate character portrait",
            inferred_modalities=[Modality.IMAGE],
            recommended_workers=["comfyui"],
            deliverables=[deliverable],
        ),
    )

    now = datetime.now(timezone.utc)
    job = service._build_job(project_id, "畫角色立繪", result, deliverable, now)

    assert job.status is GenerationJobStatus.READY, (
        f"Expected READY job for live worker, got {job.status} "
        f"with blocking_reason={job.blocking_reason!r}. "
        "Live ComfyUI (no local install) must not be blocked."
    )
    assert job.blocking_reason is None, (
        f"blocking_reason must be None for live worker, got {job.blocking_reason!r}"
    )
    assert job.worker == "comfyui"
