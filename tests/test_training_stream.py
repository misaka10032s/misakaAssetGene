"""Contract/unit tests for training progress streaming (spec §7.3 deferred tail).

The executor persists incremental status to the per-project job store; the
streaming layer pushes one event per observable change and closes on a terminal
status. These tests drive the push/stream logic with a fake job store and an
injected sleep/clock — they do NOT run a real GPU training job.

REAL-RUN: end-to-end verification against a live kohya_ss / GPT-SoVITS GPU
training run is DEFERRED to the user (no GPU available in this environment).
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest
from starlette.testclient import TestClient

import core.main as main
from core.models.schemas import (
    Modality,
    TrainingJob,
    TrainingJobStatus,
)
from core.project.manager import ProjectManager
from core.training.service import TrainingService


def _job(status: TrainingJobStatus, progress: int = 0, label: str | None = None) -> TrainingJob:
    now = datetime.now(timezone.utc)
    return TrainingJob(
        id="job1",
        project_id="proj",
        title="t",
        modality=Modality.IMAGE,
        worker="kohya-ss",
        dataset_path="ds",
        status=status,
        progress=progress,
        progress_label=label,
        created_at=now,
        updated_at=now,
    )


class _ScriptedService(TrainingService):
    """TrainingService whose poll_job replays a scripted sequence of snapshots."""

    def __init__(self, snapshots: list[TrainingJob | None]) -> None:
        # Bypass the real ProjectManager — poll_job is fully overridden.
        self._snapshots = snapshots
        self._idx = 0

    def poll_job(self, project_id: str, job_id: str):  # type: ignore[override]
        if self._idx >= len(self._snapshots):
            # Stay on the last snapshot once the script is exhausted.
            return self._snapshots[-1]
        snap = self._snapshots[self._idx]
        self._idx += 1
        return snap


# ---------------------------------------------------------------------------
# Service-level generator: emits one frame per observable change
# ---------------------------------------------------------------------------

def test_stream_emits_initial_then_changes_then_stops_on_terminal():
    snapshots = [
        _job(TrainingJobStatus.QUEUED, 0),
        _job(TrainingJobStatus.RUNNING, 10, "Step 10"),
        _job(TrainingJobStatus.RUNNING, 10, "Step 10"),   # no change -> suppressed
        _job(TrainingJobStatus.RUNNING, 50, "Step 50"),
        _job(TrainingJobStatus.COMPLETED, 100, "Completed"),
    ]
    svc = _ScriptedService(snapshots)
    frames = list(
        svc.stream_job_progress("proj", "job1", poll_interval_sec=0, sleep=lambda _: None)
    )
    statuses = [(f.status, f.progress) for f in frames]
    assert statuses == [
        (TrainingJobStatus.QUEUED, 0),
        (TrainingJobStatus.RUNNING, 10),
        (TrainingJobStatus.RUNNING, 50),
        (TrainingJobStatus.COMPLETED, 100),
    ], "duplicate snapshot must be suppressed; terminal must close the stream"


def test_stream_stops_on_failed():
    snapshots = [
        _job(TrainingJobStatus.RUNNING, 30),
        _job(TrainingJobStatus.FAILED, 30, "boom"),
        _job(TrainingJobStatus.RUNNING, 99),  # must never be reached
    ]
    svc = _ScriptedService(snapshots)
    frames = list(svc.stream_job_progress("proj", "job1", poll_interval_sec=0, sleep=lambda _: None))
    assert frames[-1].status == TrainingJobStatus.FAILED
    assert len(frames) == 2


def test_stream_returns_immediately_for_unknown_job():
    svc = _ScriptedService([None])
    frames = list(svc.stream_job_progress("proj", "missing", poll_interval_sec=0, sleep=lambda _: None))
    assert frames == []


def test_stream_respects_max_duration_deadline():
    """A job that never terminates stops once the injected clock passes the deadline."""
    running = _job(TrainingJobStatus.RUNNING, 5)
    svc = _ScriptedService([running])  # always returns the same RUNNING snapshot

    ticks = iter([0.0, 0.0, 100.0, 200.0])  # clock advances past the 50s deadline

    def fake_clock() -> float:
        try:
            return next(ticks)
        except StopIteration:
            return 999.0

    frames = list(
        svc.stream_job_progress(
            "proj",
            "job1",
            poll_interval_sec=0,
            max_duration_sec=50.0,
            sleep=lambda _: None,
            clock=fake_clock,
        )
    )
    # Only the first (initial) frame is emitted; deadline then closes the stream.
    assert len(frames) == 1
    assert frames[0].status == TrainingJobStatus.RUNNING


# ---------------------------------------------------------------------------
# API-level: SSE endpoint shape + framing
# ---------------------------------------------------------------------------

@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    manager = ProjectManager(tmp_path / "projects")
    monkeypatch.setattr(main, "project_manager", manager)
    monkeypatch.setattr(main.generation_service, "project_manager", manager)
    monkeypatch.setattr(main.training_service, "project_manager", manager)
    return TestClient(main.app, base_url="http://127.0.0.1:8401")


def _create_project(client: TestClient) -> str:
    resp = client.post("/api/v1/projects", json={"name": "StreamProj", "type": "RPG", "synopsis": "s"})
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]["project"]["id"]


def test_stream_endpoint_unknown_project_returns_404(client: TestClient) -> None:
    resp = client.get("/api/v1/projects/no-such/training/job1/stream")
    assert resp.status_code == 404


def test_stream_endpoint_unknown_job_returns_404(client: TestClient) -> None:
    project_id = _create_project(client)
    resp = client.get(f"/api/v1/projects/{project_id}/training/missing/stream")
    assert resp.status_code == 404


def test_stream_endpoint_emits_sse_frames(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """The endpoint streams text/event-stream frames ending with an event: done."""
    project_id = _create_project(client)

    # NOTE: the route makes one poll_job call up-front for the 404 pre-check,
    # which consumes the first scripted snapshot; the stream then replays the
    # rest. Lead with an extra RUNNING so the stream observes RUNNING then
    # COMPLETED after the pre-check.
    scripted = [
        _job(TrainingJobStatus.RUNNING, 20, "Step 20"),   # consumed by 404 pre-check
        _job(TrainingJobStatus.RUNNING, 20, "Step 20"),   # initial stream frame
        _job(TrainingJobStatus.COMPLETED, 100, "Completed"),
    ]
    seq = iter(scripted)
    last = {"job": scripted[-1]}

    def fake_poll(_pid: str, _jid: str):
        try:
            last["job"] = next(seq)
        except StopIteration:
            pass
        return last["job"]

    monkeypatch.setattr(main.training_service, "poll_job", fake_poll)
    # Avoid real sleeping between frames in the stream generator.
    monkeypatch.setattr(
        main.training_service,
        "stream_job_progress",
        lambda pid, jid, **kw: TrainingService.stream_job_progress(
            main.training_service, pid, jid, poll_interval_sec=0, sleep=lambda _: None
        ),
    )

    resp = client.get(f"/api/v1/projects/{project_id}/training/job1/stream")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")
    body = resp.text
    assert "event: progress" in body
    assert "event: done" in body
    assert '"status": "completed"' in body
