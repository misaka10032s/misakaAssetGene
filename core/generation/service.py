from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from core.generation.adapters import get_adapter
from core.generation.adapters.common import AdapterContext
from core.models.schemas import (
    AssetRecord,
    BatchExecuteRequest,
    ClarifyResult,
    ConsultantPlanRecord,
    ConsultantDeliverable,
    GenerationJob,
    GenerationRecipe,
    GenerationJobStatus,
    JobExecutionPatch,
    Modality,
    ProjectWorkspaceData,
)
from core.project.manager import ProjectManager
from core.integration.workers import WorkersService
from core.models.schemas import (
    LicenseReportEntry,
    ProjectLicenseReport,
    ProjectVersionEdge,
    ProjectVersionGraph,
    ProjectVersionNode,
)


class GenerationService:
    def __init__(self, project_manager: ProjectManager, workers_service: WorkersService) -> None:
        self.project_manager = project_manager
        self.workers_service = workers_service

    def list_workspace(self, project_id: str) -> ProjectWorkspaceData:
        _, project_dir = self.project_manager.get_project(project_id)
        jobs = self._refresh_jobs(self._read_jobs(project_dir))
        self._write_jobs(project_dir, jobs)
        return ProjectWorkspaceData(
            jobs=jobs,
            assets=self._read_assets(project_dir),
            plans=self._read_plans(project_dir),
        )

    def record_plan(self, project_id: str, prompt: str, result: ClarifyResult) -> ProjectWorkspaceData:
        _, project_dir = self.project_manager.get_project(project_id)
        jobs = self._read_jobs(project_dir)
        assets = self._read_assets(project_dir)
        plans = self._read_plans(project_dir)
        now = datetime.now(timezone.utc)

        plan_record = self._write_plan_record(project_dir, prompt, result, now)
        plans.append(plan_record)

        if result.analysis:
            for deliverable in result.analysis.deliverables:
                jobs.append(self._build_job(project_id, prompt, result, deliverable, now))

        self._write_jobs(project_dir, jobs)
        self._write_assets(project_dir, assets)
        self._write_plans(project_dir, plans)
        return ProjectWorkspaceData(jobs=jobs, assets=assets, plans=plans)

    def execute_job(self, project_id: str, job_id: str) -> ProjectWorkspaceData:
        _, project_dir = self.project_manager.get_project(project_id)
        jobs = self._refresh_jobs(self._read_jobs(project_dir))
        assets = self._read_assets(project_dir)
        plans = self._read_plans(project_dir)

        target_index = next((index for index, job in enumerate(jobs) if job.id == job_id), None)
        if target_index is None:
            raise FileNotFoundError(f"Job not found: {job_id}")

        job = jobs[target_index]
        if job.status == GenerationJobStatus.BLOCKED:
            raise ValueError(job.blocking_reason or "Job is blocked.")
        if job.status == GenerationJobStatus.RUNNING:
            raise ValueError("Job is already running.")
        if job.status == GenerationJobStatus.COMPLETED:
            return ProjectWorkspaceData(jobs=jobs, assets=assets, plans=plans)
        if job.status == GenerationJobStatus.FAILED:
            raise ValueError("Job has failed. Re-run is not implemented yet.")

        jobs, assets = self._execute_job_in_memory(project_dir, jobs, assets, target_index)
        plans = self._read_plans(project_dir)
        return ProjectWorkspaceData(jobs=jobs, assets=assets, plans=plans)

    def execute_ready_jobs(self, project_id: str, job_ids: list[str] | None = None) -> ProjectWorkspaceData:
        _, project_dir = self.project_manager.get_project(project_id)
        jobs = self._refresh_jobs(self._read_jobs(project_dir))
        assets = self._read_assets(project_dir)
        plans = self._read_plans(project_dir)
        requested_ids = set(job_ids or [])
        for index, job in enumerate(list(jobs)):
            if job.status not in {GenerationJobStatus.READY, GenerationJobStatus.PLANNED}:
                continue
            if requested_ids and job.id not in requested_ids:
                continue
            jobs, assets = self._execute_job_in_memory(project_dir, jobs, assets, index)
        return ProjectWorkspaceData(jobs=jobs, assets=assets, plans=plans)

    def update_job(self, project_id: str, job_id: str, patch: JobExecutionPatch) -> ProjectWorkspaceData:
        _, project_dir = self.project_manager.get_project(project_id)
        jobs = self._read_jobs(project_dir)
        assets = self._read_assets(project_dir)
        plans = self._read_plans(project_dir)
        target_index = next((index for index, job in enumerate(jobs) if job.id == job_id), None)
        if target_index is None:
            raise FileNotFoundError(f"Job not found: {job_id}")
        current = jobs[target_index]
        updated_job = current.model_copy(
            update={
                "worker": patch.worker or current.worker,
                "recipe": patch.recipe,
                "source_asset_id": patch.source_asset_id,
                "mask_asset_id": patch.mask_asset_id,
                "updated_at": datetime.now(timezone.utc),
            }
        )
        jobs[target_index] = updated_job
        jobs = self._refresh_jobs(jobs)
        self._write_jobs(project_dir, jobs)
        return ProjectWorkspaceData(jobs=jobs, assets=assets, plans=plans)

    def import_asset(
        self,
        project_id: str,
        *,
        filename: str,
        content: bytes,
        modality: Modality,
        asset_type: str,
        title: str,
        description: str = "",
    ) -> ProjectWorkspaceData:
        _, project_dir = self.project_manager.get_project(project_id)
        jobs = self._read_jobs(project_dir)
        assets = self._read_assets(project_dir)
        plans = self._read_plans(project_dir)
        safe_name = Path(filename).name or f"{uuid.uuid4().hex}.bin"
        artifact = type("Artifact", (), {
            "modality": modality,
            "asset_type": asset_type,
            "title": title,
            "filename": safe_name,
            "description": description,
            "content": content,
            "source_path": None,
        })()
        assets.append(self._persist_generated_artifact(project_dir, None, artifact))
        self._write_assets(project_dir, assets)
        return ProjectWorkspaceData(jobs=jobs, assets=assets, plans=plans)

    def build_version_graph(self, project_id: str) -> ProjectVersionGraph:
        _, project_dir = self.project_manager.get_project(project_id)
        jobs = self._read_jobs(project_dir)
        assets = self._read_assets(project_dir)
        nodes: list[ProjectVersionNode] = []
        edges: list[ProjectVersionEdge] = []

        for job in jobs:
            job_node_id = f"job:{job.id}"
            nodes.append(
                ProjectVersionNode(
                    id=job_node_id,
                    title=job.title,
                    node_type="job",
                    modality=job.modality,
                    status=job.status.value,
                    worker=job.worker,
                    created_at=job.created_at,
                )
            )
            if job.source_asset_id:
                edges.append(ProjectVersionEdge(source=f"asset:{job.source_asset_id}", target=job_node_id, relation="source"))
            if job.mask_asset_id:
                edges.append(ProjectVersionEdge(source=f"asset:{job.mask_asset_id}", target=job_node_id, relation="mask"))

        for asset in assets:
            asset_node_id = f"asset:{asset.id}"
            nodes.append(
                ProjectVersionNode(
                    id=asset_node_id,
                    title=asset.title,
                    node_type="asset",
                    modality=asset.modality,
                    status=asset.asset_type,
                    worker=None,
                    created_at=asset.created_at,
                )
            )
            if asset.job_id:
                edges.append(ProjectVersionEdge(source=f"job:{asset.job_id}", target=asset_node_id, relation="output"))

        nodes.sort(key=lambda node: node.created_at)
        return ProjectVersionGraph(nodes=nodes, edges=edges)

    def _build_job(
        self,
        project_id: str,
        prompt: str,
        result: ClarifyResult,
        deliverable: ConsultantDeliverable,
        now: datetime,
    ) -> GenerationJob:
        blocking_reason = self._build_blocking_reason(deliverable.worker, result)
        return GenerationJob(
            id=uuid.uuid4().hex,
            project_id=project_id,
            title=deliverable.title,
            modality=deliverable.modality,
            asset_type=deliverable.asset_type,
            status=GenerationJobStatus.BLOCKED if blocking_reason else GenerationJobStatus.READY,
            prompt=prompt,
            summary=result.summary,
            worker=deliverable.worker,
            variants=list(deliverable.variants),
            recipe=GenerationRecipe.AUTO if deliverable.modality in {Modality.IMAGE, Modality.VIDEO} else None,
            blocking_reason=blocking_reason,
            last_error=None,
            progress=0,
            progress_label=None,
            search_queries=list(result.analysis.search_queries if result.analysis else []),
            steps=list(result.analysis.execution_steps if result.analysis else []),
            created_at=now,
            updated_at=now,
        )

    def _run_job(self, project_dir: Path, job: GenerationJob):
        if not job.worker:
            raise RuntimeError("Job has no assigned worker.")
        adapter = get_adapter(job.worker)
        if adapter is None:
            raise RuntimeError(f"Execution adapter is not implemented for worker {job.worker}.")
        worker_snapshot = self.workers_service.get_worker(job.worker)
        if not worker_snapshot.is_running:
            raise RuntimeError(worker_snapshot.readiness_note or f"{worker_snapshot.display_name} is not running.")
        source_asset_path = self._resolve_job_asset_path(project_dir, job.source_asset_id)
        mask_asset_path = self._resolve_job_asset_path(project_dir, job.mask_asset_id)
        return adapter(
            AdapterContext(
                project_dir=project_dir,
                job=job,
                worker_path=Path(worker_snapshot.path),
                health_check=worker_snapshot.health_check,
                source_asset_path=source_asset_path,
                mask_asset_path=mask_asset_path,
            )
        )

    def _persist_generated_artifact(self, project_dir: Path, job: GenerationJob | None, artifact) -> AssetRecord:
        modality_dirs = {
            Modality.IMAGE: Path("assets") / "images",
            Modality.MUSIC: Path("assets") / "audio",
            Modality.VOICE: Path("assets") / "audio",
            Modality.VIDEO: Path("assets") / "video",
            Modality.TEXT: Path("assets") / "text",
        }
        target_dir = project_dir / modality_dirs.get(artifact.modality, Path("assets") / "text")
        target_dir.mkdir(parents=True, exist_ok=True)
        target_path = target_dir / artifact.filename
        if artifact.content is not None:
            target_path.write_bytes(artifact.content)
        elif artifact.source_path is not None:
            target_path.write_bytes(artifact.source_path.read_bytes())
        else:
            raise RuntimeError("Generated artifact has no content.")
        return AssetRecord(
            id=uuid.uuid4().hex,
            job_id=job.id if job else None,
            modality=artifact.modality,
            asset_type=artifact.asset_type,
            title=artifact.title,
            path=str(target_path.relative_to(project_dir)),
            description=artifact.description,
            created_at=datetime.now(timezone.utc),
        )

    def _build_blocking_reason(self, worker_name: str | None, result: ClarifyResult) -> str | None:
        if result.analysis and result.analysis.required_research:
            return result.analysis.required_research[0]
        return self._build_worker_blocking_reason(worker_name)

    def _build_worker_blocking_reason(self, worker_name: str | None) -> str | None:
        if not worker_name:
            return None
        readiness_note = self.workers_service.readiness_note(worker_name)
        if readiness_note:
            return readiness_note
        snapshot = self.workers_service.get_worker(worker_name)
        if not snapshot.is_installed:
            return f"{snapshot.display_name} is not installed yet."
        if not snapshot.is_running:
            return f"{snapshot.display_name} is installed but not running."
        return None

    def _resolve_job_asset_path(self, project_dir: Path, asset_id: str | None) -> Path | None:
        if not asset_id:
            return None
        assets = self._read_assets(project_dir)
        asset = next((item for item in assets if item.id == asset_id), None)
        if asset is None:
            raise RuntimeError(f"Referenced asset not found: {asset_id}")
        asset_path = project_dir / asset.path
        if not asset_path.exists():
            raise RuntimeError(f"Referenced asset file is missing: {asset.path}")
        return asset_path

    def _execute_job_in_memory(
        self,
        project_dir: Path,
        jobs: list[GenerationJob],
        assets: list[AssetRecord],
        target_index: int,
    ) -> tuple[list[GenerationJob], list[AssetRecord]]:
        job = jobs[target_index]
        now = datetime.now(timezone.utc)
        running_job = job.model_copy(
            update={
                "status": GenerationJobStatus.RUNNING,
                "blocking_reason": None,
                "last_error": None,
                "progress": 5,
                "progress_label": "Starting execution",
                "updated_at": now,
            }
        )
        jobs[target_index] = running_job
        self._write_jobs(project_dir, jobs)

        def report_progress(progress: int, label: str) -> None:
            nonlocal jobs
            jobs[target_index] = jobs[target_index].model_copy(
                update={
                    "progress": max(0, min(progress, 100)),
                    "progress_label": label,
                    "updated_at": datetime.now(timezone.utc),
                }
            )
            self._write_jobs(project_dir, jobs)

        try:
            self.workers_service.mark_worker_activity(job.worker, active=True)
            execution_result = self._run_job_with_progress(project_dir, jobs[target_index], report_progress)
        except Exception as exc:
            self.workers_service.mark_worker_activity(job.worker, active=False)
            jobs[target_index] = jobs[target_index].model_copy(
                update={
                    "status": GenerationJobStatus.FAILED,
                    "blocking_reason": str(exc),
                    "last_error": str(exc),
                    "progress": 0,
                    "progress_label": None,
                    "updated_at": datetime.now(timezone.utc),
                }
            )
            self._write_jobs(project_dir, jobs)
            return jobs, assets

        created_assets = [self._persist_generated_artifact(project_dir, jobs[target_index], artifact) for artifact in execution_result.artifacts]
        assets.extend(created_assets)
        self.workers_service.mark_worker_activity(job.worker, active=False)
        jobs[target_index] = jobs[target_index].model_copy(
            update={
                "status": GenerationJobStatus.COMPLETED,
                "progress": 100,
                "progress_label": "Completed",
                "updated_at": datetime.now(timezone.utc),
            }
        )
        self._write_jobs(project_dir, jobs)
        self._write_assets(project_dir, assets)
        return jobs, assets

    def _run_job_with_progress(self, project_dir: Path, job: GenerationJob, report_progress) -> object:
        if not job.worker:
            raise RuntimeError("Job has no assigned worker.")
        adapter = get_adapter(job.worker)
        if adapter is None:
            raise RuntimeError(f"Execution adapter is not implemented for worker {job.worker}.")
        worker_snapshot = self.workers_service.get_worker(job.worker)
        if not worker_snapshot.is_running:
            raise RuntimeError(worker_snapshot.readiness_note or f"{worker_snapshot.display_name} is not running.")
        source_asset_path = self._resolve_job_asset_path(project_dir, job.source_asset_id)
        mask_asset_path = self._resolve_job_asset_path(project_dir, job.mask_asset_id)
        return adapter(
            AdapterContext(
                project_dir=project_dir,
                job=job,
                worker_path=Path(worker_snapshot.path),
                health_check=worker_snapshot.health_check,
                source_asset_path=source_asset_path,
                mask_asset_path=mask_asset_path,
                report_progress=report_progress,
            )
        )

    def _refresh_jobs(self, jobs: list[GenerationJob]) -> list[GenerationJob]:
        refreshed: list[GenerationJob] = []
        for job in jobs:
            if job.status in {
                GenerationJobStatus.RUNNING,
                GenerationJobStatus.COMPLETED,
                GenerationJobStatus.FAILED,
            }:
                refreshed.append(job)
                continue
            runtime_block = self._build_worker_blocking_reason(job.worker)
            if runtime_block:
                if job.status != GenerationJobStatus.BLOCKED or self._is_worker_blocking_reason(job.blocking_reason):
                    refreshed.append(
                        job.model_copy(
                            update={
                                "status": GenerationJobStatus.BLOCKED,
                                "blocking_reason": runtime_block,
                                "updated_at": datetime.now(timezone.utc),
                            }
                        )
                    )
                    continue
            elif self._is_worker_blocking_reason(job.blocking_reason):
                refreshed.append(
                    job.model_copy(
                        update={
                            "status": GenerationJobStatus.READY,
                            "blocking_reason": None,
                            "updated_at": datetime.now(timezone.utc),
                        }
                    )
                )
                continue
            refreshed.append(job)
        return refreshed

    def _is_worker_blocking_reason(self, blocking_reason: str | None) -> bool:
        if not blocking_reason:
            return False
        lowered = blocking_reason.lower()
        return any(
            marker in lowered
            for marker in [
                "worker server is not running",
                "installed but not running",
                "is not installed yet",
                "repository is not installed",
                "no comfyui checkpoint is installed",
            ]
        )

    def _write_plan_record(
        self,
        project_dir: Path,
        prompt: str,
        result: ClarifyResult,
        now: datetime,
    ) -> ConsultantPlanRecord:
        plan_id = uuid.uuid4().hex
        relative_path = Path(".cache") / "consultant" / "plans" / f"{now.strftime('%Y%m%d-%H%M%S')}-{plan_id}.md"
        absolute_path = project_dir / relative_path
        absolute_path.parent.mkdir(parents=True, exist_ok=True)
        analysis = result.analysis
        lines = [
            f"# Consultant Plan",
            "",
            f"- Prompt: {prompt}",
            f"- Modality: {result.modality.value}",
            f"- Summary: {result.summary}",
            f"- Next step: {result.next_step}",
            "",
        ]
        if analysis:
            lines.append(f"- Modalities: {', '.join(modality.value for modality in analysis.inferred_modalities)}")
            if analysis.franchise:
                lines.append(f"- Franchise: {analysis.franchise}")
            if analysis.characters:
                lines.append(f"- Characters: {', '.join(analysis.characters)}")
            if analysis.outfits:
                lines.append(f"- Outfits: {', '.join(analysis.outfits)}")
            if analysis.matrix_axes:
                lines.append(f"- Matrix axes: {', '.join(analysis.matrix_axes)}")
            if analysis.recommended_workers:
                lines.append(f"- Recommended workers: {', '.join(analysis.recommended_workers)}")
            lines.append("")
            if analysis.required_research:
                lines.append("## Required Research")
                lines.extend([f"- {item}" for item in analysis.required_research])
                lines.append("")
            if analysis.search_queries:
                lines.append("## Search Queries")
                lines.extend([f"- {item}" for item in analysis.search_queries])
                lines.append("")
            if analysis.execution_steps:
                lines.append("## Execution Steps")
                lines.extend([f"- {step.title}: {step.detail}" for step in analysis.execution_steps])
                lines.append("")
            if analysis.guidance_path:
                lines.append("## Guidance Path")
                lines.extend([f"- {item}" for item in analysis.guidance_path])
                lines.append("")
        absolute_path.write_text("\n".join(lines).strip() + "\n", encoding="utf-8")
        return ConsultantPlanRecord(
            id=plan_id,
            title="Consultant plan",
            path=str(relative_path),
            summary=result.summary,
            prompt=prompt,
            modalities=list(analysis.inferred_modalities if analysis else [result.modality]),
            created_at=now,
        )

    def _jobs_path(self, project_dir: Path) -> Path:
        return project_dir / "jobs.json"

    def _assets_path(self, project_dir: Path) -> Path:
        return project_dir / "assets" / "index.json"

    def _plans_path(self, project_dir: Path) -> Path:
        return project_dir / ".cache" / "consultant" / "index.json"

    def _read_jobs(self, project_dir: Path) -> list[GenerationJob]:
        path = self._jobs_path(project_dir)
        if not path.exists():
            return []
        payload = json.loads(path.read_text(encoding="utf-8"))
        return [GenerationJob(**item) for item in payload.get("jobs", [])]

    def _write_jobs(self, project_dir: Path, jobs: list[GenerationJob]) -> None:
        self._jobs_path(project_dir).write_text(
            json.dumps({"jobs": [job.model_dump(mode="json") for job in jobs]}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def _read_assets(self, project_dir: Path) -> list[AssetRecord]:
        path = self._assets_path(project_dir)
        if not path.exists():
            return []
        payload = json.loads(path.read_text(encoding="utf-8"))
        assets = [AssetRecord(**item) for item in payload.get("assets", [])]
        return [asset for asset in assets if asset.asset_type != "consultant_plan"]

    def _write_assets(self, project_dir: Path, assets: list[AssetRecord]) -> None:
        path = self._assets_path(project_dir)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"assets": [asset.model_dump(mode="json") for asset in assets]}, ensure_ascii=False, indent=2)
            + "\n",
            encoding="utf-8",
        )

    def _read_plans(self, project_dir: Path) -> list[ConsultantPlanRecord]:
        plans: list[ConsultantPlanRecord] = []
        path = self._plans_path(project_dir)
        if path.exists():
            payload = json.loads(path.read_text(encoding="utf-8"))
            plans.extend(ConsultantPlanRecord(**item) for item in payload.get("plans", []))
        legacy_assets_path = self._assets_path(project_dir)
        if legacy_assets_path.exists():
            payload = json.loads(legacy_assets_path.read_text(encoding="utf-8"))
            for item in payload.get("assets", []):
                if item.get("asset_type") != "consultant_plan":
                    continue
                plans.append(
                    ConsultantPlanRecord(
                        id=str(item.get("id") or uuid.uuid4().hex),
                        title=str(item.get("title") or "Consultant plan"),
                        path=str(item.get("path") or ""),
                        summary=str(item.get("description") or ""),
                        prompt="",
                        modalities=[Modality.TEXT],
                        created_at=item.get("created_at"),
                    )
                )
        return sorted(plans, key=lambda plan: plan.created_at, reverse=True)

    def _write_plans(self, project_dir: Path, plans: list[ConsultantPlanRecord]) -> None:
        path = self._plans_path(project_dir)
        path.parent.mkdir(parents=True, exist_ok=True)
        ordered_plans = sorted(plans, key=lambda plan: plan.created_at, reverse=True)
        path.write_text(
            json.dumps({"plans": [plan.model_dump(mode="json") for plan in ordered_plans]}, ensure_ascii=False, indent=2)
            + "\n",
            encoding="utf-8",
        )
