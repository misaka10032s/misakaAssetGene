from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from core.models.schemas import Modality, TrainingJob, TrainingJobCreateRequest, TrainingJobStatus, TrainingWorkspaceData
from core.project.manager import ProjectManager


class TrainingService:
    def __init__(self, project_manager: ProjectManager) -> None:
        self.project_manager = project_manager

    def list_jobs(self, project_id: str) -> TrainingWorkspaceData:
        _, project_dir = self.project_manager.get_project(project_id)
        return TrainingWorkspaceData(jobs=self._read_jobs(project_dir))

    def submit_job(self, project_id: str, payload: TrainingJobCreateRequest) -> TrainingWorkspaceData:
        _, project_dir = self.project_manager.get_project(project_id)
        jobs = self._read_jobs(project_dir)
        now = datetime.now(timezone.utc)
        modality = payload.modality
        default_worker = "kohya-ss" if modality == Modality.IMAGE else "gpt-sovits-train"
        jobs.append(
            TrainingJob(
                id=uuid.uuid4().hex,
                project_id=project_id,
                title=payload.title.strip(),
                modality=payload.modality,
                worker=payload.worker.strip() if payload.worker else default_worker,
                dataset_path=payload.dataset_path.strip(),
                status=TrainingJobStatus.PLANNED,
                note="Training orchestration scaffold created. Runner bridge is the next step.",
                created_at=now,
                updated_at=now,
            )
        )
        self._write_jobs(project_dir, jobs)
        return TrainingWorkspaceData(jobs=jobs)

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
