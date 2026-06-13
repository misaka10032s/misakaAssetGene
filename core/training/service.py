from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from core.models.schemas import Modality, TrainingJob, TrainingJobCreateRequest, TrainingJobStatus, TrainingWorkspaceData
from core.project.manager import ProjectManager
from core.training.executor import TrainingExecutor


class TrainingService:
    def __init__(
        self,
        project_manager: ProjectManager,
        executor: TrainingExecutor | None = None,
    ) -> None:
        self.project_manager = project_manager
        # Executor is optional: if not wired at construction time the service
        # falls back to PLANNED status (no-op, backward-compatible).
        # In main.py the executor is created and injected after the scheduler
        # is set up.  Tests may inject a FakeRunner-backed executor.
        self._executor = executor

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def list_jobs(self, project_id: str) -> TrainingWorkspaceData:
        _, project_dir = self.project_manager.get_project(project_id)
        return TrainingWorkspaceData(jobs=self._read_jobs(project_dir))

    def submit_job(self, project_id: str, payload: TrainingJobCreateRequest) -> TrainingWorkspaceData:
        _, project_dir = self.project_manager.get_project(project_id)
        jobs = self._read_jobs(project_dir)
        now = datetime.now(timezone.utc)
        modality = payload.modality
        default_worker = "kohya-ss" if modality == Modality.IMAGE else "gpt-sovits"
        job = TrainingJob(
            id=uuid.uuid4().hex,
            project_id=project_id,
            title=payload.title.strip(),
            modality=payload.modality,
            worker=payload.worker.strip() if payload.worker else default_worker,
            dataset_path=payload.dataset_path.strip(),
            status=TrainingJobStatus.PLANNED,
            note="Job created; will be enqueued for execution.",
            created_at=now,
            updated_at=now,
        )
        jobs.append(job)
        self._write_jobs(project_dir, jobs)

        # Enqueue for execution if executor is wired.
        if self._executor is not None:
            self._executor.enqueue(project_id, job.id)
            # Refresh jobs (enqueue transitions job to QUEUED).
            jobs = self._read_jobs(project_dir)

        return TrainingWorkspaceData(jobs=jobs)

    def cancel_job(self, project_id: str, job_id: str) -> bool:
        """Cancel a queued or running training job.

        Returns True if cancellation was initiated, False if the executor is
        not wired or the job was not found in a cancellable state.
        """
        if self._executor is None:
            return False
        return self._executor.cancel_job(project_id, job_id)

    def poll_job(self, project_id: str, job_id: str) -> TrainingJob | None:
        """Return the latest status of a single job, or None if not found."""
        _, project_dir = self.project_manager.get_project(project_id)
        jobs = self._read_jobs(project_dir)
        for j in jobs:
            if j.id == job_id:
                return j
        return None

    def set_executor(self, executor: TrainingExecutor) -> None:
        """Wire the executor after construction (used in main.py startup)."""
        self._executor = executor

    # ------------------------------------------------------------------
    # Storage helpers
    # ------------------------------------------------------------------

    def _jobs_path(self, project_dir: Path) -> Path:
        return project_dir / ".cache" / "training" / "jobs.json"

    def _read_jobs(self, project_dir: Path) -> list[TrainingJob]:
        path = self._jobs_path(project_dir)
        if not path.exists():
            return []
        payload = json.loads(path.read_text(encoding="utf-8"))
        return [TrainingJob(**item) for item in payload.get("jobs", [])]

    def _write_jobs(self, project_dir: Path, jobs: list[TrainingJob]) -> None:
        path = self._jobs_path(project_dir)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"jobs": [job.model_dump(mode="json") for job in jobs]}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
