from __future__ import annotations

import hashlib
import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger("misaka.generation.service")

from core.generation.adapters import get_adapter
from core.generation.adapters.common import AdapterContext
from core.models.schemas import (
    AssetRecord,
    BatchExecuteData,
    BatchExecuteRequest,
    ClarifyResult,
    ConsultantDeliverable,
    ConsultantPlanRecord,
    ConsultantPlanStep,
    GenerationJob,
    GenerationRecipe,
    GenerationJobStatus,
    JobExecutionPatch,
    Modality,
    ProjectWorkspaceData,
    RefineRequest,
    SkippedJobInfo,
)
from core.generation import refine as refine_planner
from core.project.manager import ProjectManager
from core.integration.workers import WorkersService
from core.models.schemas import (
    LicenseReportEntry,
    ProjectLicenseReport,
    ProjectVersionEdge,
    ProjectVersionGraph,
    ProjectVersionNode,
    VersionDiffData,
    VersionTreeData,
    VersionTreeNode,
)
from core.scheduler.vram import ModelScheduler

# Blocking reason token used when the VRAM training lock is held.
# The generation _refresh_jobs path recognises this prefix and clears it
# automatically once is_training_locked() returns False.
_TRAINING_LOCK_REASON = "training in progress — generation queued"


def _prompt_hash(prompt: str | None) -> str | None:
    """Stable sha256 of the prompt, written into asset metadata (spec §8.1)."""
    if not prompt:
        return None
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()


class GenerationService:
    def __init__(
        self,
        project_manager: ProjectManager,
        workers_service: WorkersService,
        scheduler: ModelScheduler | None = None,
    ) -> None:
        self.project_manager = project_manager
        self.workers_service = workers_service
        # Optional: when the VRAM scheduler is wired, generation dispatch
        # checks is_training_locked() before running any job (spec §7.3).
        self._scheduler = scheduler

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

        # Guard: refuse generation while the VRAM training lock is held (spec §7.3).
        training_block = self._training_lock_blocking_reason()
        if training_block:
            raise ValueError(training_block)

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

    def execute_ready_jobs(self, project_id: str, job_ids: list[str] | None = None) -> BatchExecuteData:
        """Execute all ready/planned jobs in the requested set (spec §5.14).

        Blocked jobs within the requested set are collected into ``skipped``
        rather than silently ignored, so callers can surface a truthful summary
        (e.g. "executed 2, skipped 1: reason…").

        If the VRAM training lock is held (spec §7.3), ALL jobs in the set are
        skipped with the training-locked blocking reason rather than proceeding.
        """
        _, project_dir = self.project_manager.get_project(project_id)
        jobs = self._refresh_jobs(self._read_jobs(project_dir))
        assets = self._read_assets(project_dir)
        plans = self._read_plans(project_dir)
        requested_ids = set(job_ids or [])
        executed_count = 0
        skipped: list[SkippedJobInfo] = []

        # Guard: if training lock is held, skip everything in this batch.
        training_block = self._training_lock_blocking_reason()

        for index, job in enumerate(list(jobs)):
            # Skip jobs not in the requested set (if a set was given).
            if requested_ids and job.id not in requested_ids:
                continue
            if training_block:
                skipped.append(
                    SkippedJobInfo(
                        job_id=job.id,
                        title=job.title,
                        reason=training_block,
                    )
                )
                continue
            if job.status == GenerationJobStatus.BLOCKED:
                skipped.append(
                    SkippedJobInfo(
                        job_id=job.id,
                        title=job.title,
                        reason=job.blocking_reason or "Blocked",
                    )
                )
                continue
            if job.status not in {GenerationJobStatus.READY, GenerationJobStatus.PLANNED}:
                continue
            jobs, assets = self._execute_job_in_memory(project_dir, jobs, assets, index)
            executed_count += 1
        workspace = ProjectWorkspaceData(jobs=jobs, assets=assets, plans=plans)
        return BatchExecuteData(workspace=workspace, executed_count=executed_count, skipped=skipped)

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

    def refine_asset(self, project_id: str, parent_asset_id: str, request: RefineRequest) -> ProjectWorkspaceData:
        """Create a refine job from a parent image version (spec §5.11 / §6.2).

        The §6.2 decision tree selects the minimal sufficient strategy; the job
        records parent-child lineage, the chosen recipe/params, the prompt
        delta and the rationale. ``metadata_only`` edits never touch a worker
        and complete immediately. All other strategies become a READY (or
        worker-BLOCKED) generation job that the existing execute path runs.
        """
        _, project_dir = self.project_manager.get_project(project_id)
        jobs = self._read_jobs(project_dir)
        assets = self._read_assets(project_dir)
        plans = self._read_plans(project_dir)

        parent = next((item for item in assets if item.id == parent_asset_id), None)
        if parent is None:
            raise FileNotFoundError(f"Parent asset not found: {parent_asset_id}")
        if parent.modality is not Modality.IMAGE:
            raise ValueError("Refine is only supported for image assets in M2.")

        plan = refine_planner.plan_refine(request)
        now = datetime.now(timezone.utc)
        title = request.title or f"{parent.title} (refine)"

        # Cheapest rung: metadata-only edits mutate the parent record's lineage
        # markers without re-rendering anything (spec §6.2 first rung).
        if plan.recipe is None:
            # Apply the metadata delta to the parent AssetRecord and persist it.
            parent_index = next(i for i, a in enumerate(assets) if a.id == parent_asset_id)
            metadata_delta = self._extract_metadata_delta(request.instruction)
            updated_tags = list(dict.fromkeys(parent.tags + metadata_delta.get("tags", [])))
            updated_parent = parent.model_copy(update={
                "tags": updated_tags,
                "user_note": metadata_delta.get("user_note") or parent.user_note,
                "is_favorite": metadata_delta.get("is_favorite", parent.is_favorite),
            })
            assets[parent_index] = updated_parent
            self._write_assets(project_dir, assets)

            metadata_job = GenerationJob(
                id=uuid.uuid4().hex,
                project_id=project_id,
                title=title,
                modality=Modality.IMAGE,
                asset_type="image",
                status=GenerationJobStatus.COMPLETED,
                prompt=request.instruction,
                summary=plan.reason,
                worker=None,
                recipe=None,
                source_asset_id=parent_asset_id,
                parent_asset_id=parent_asset_id,
                refine_strategy=plan.strategy,
                refine_reason=plan.reason,
                prompt_delta=plan.prompt_delta,
                param_delta=plan.param_delta,
                progress=100,
                progress_label="Metadata updated",
                created_at=now,
                updated_at=now,
            )
            jobs.append(metadata_job)
            self._write_jobs(project_dir, jobs)
            return ProjectWorkspaceData(jobs=jobs, assets=assets, plans=plans)

        if plan.requires_mask and not request.mask_asset_id:
            blocking_reason = "Inpaint refine requires a mask. Paint or select a region first."
        else:
            blocking_reason = self._build_worker_blocking_reason("comfyui")

        decomposition_steps = [
            ConsultantPlanStep(title=step.stage.value, detail=step.prompt, worker="comfyui")
            for step in plan.decomposition
        ]

        refine_job = GenerationJob(
            id=uuid.uuid4().hex,
            project_id=project_id,
            title=title,
            modality=Modality.IMAGE,
            asset_type="image",
            status=GenerationJobStatus.BLOCKED if blocking_reason else GenerationJobStatus.READY,
            prompt=request.instruction,
            summary=plan.reason,
            worker="comfyui",
            recipe=plan.recipe,
            source_asset_id=parent_asset_id,
            mask_asset_id=request.mask_asset_id,
            parent_asset_id=parent_asset_id,
            refine_strategy=plan.strategy,
            refine_reason=plan.reason,
            prompt_delta=plan.prompt_delta,
            param_delta=plan.param_delta,
            params=plan.params,
            blocking_reason=blocking_reason,
            steps=decomposition_steps,
            created_at=now,
            updated_at=now,
        )
        jobs.append(refine_job)
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
            # Parent-child refine lineage (spec §5.11 / §8.1): draw a direct
            # edge from the parent version to this refined version.
            if asset.parent_version_id:
                edges.append(
                    ProjectVersionEdge(
                        source=f"asset:{asset.parent_version_id}",
                        target=asset_node_id,
                        relation="refine",
                    )
                )

        nodes.sort(key=lambda node: node.created_at)
        return ProjectVersionGraph(nodes=nodes, edges=edges)

    # ------------------------------------------------------------------
    # M5.1 — Version Tree DAG  (spec §8.2)
    # ------------------------------------------------------------------

    # Maximum number of nodes returned by build_version_tree.
    # Protects against enormous projects; callers get ``capped=True`` when hit.
    _TREE_NODE_CAP: int = 2000

    def build_version_tree(self, project_id: str) -> VersionTreeData:
        """Build the parent-child DAG of asset versions for a project (spec §8.2 / M5.1).

        Robustness guarantees:
        - Cycle detection: a malformed parent_version_id chain is detected by
          tracking already-visited ids during ancestor traversal.  Cycles are
          surfaced via ``cycle_detected=True``; no infinite loop occurs.
        - Orphan handling: when ``parent_version_id`` points to a missing asset,
          the node's ``is_orphaned`` flag is set to True and it is still included
          in the response.  The missing parent is NOT fabricated.
        - Node cap: at most ``_TREE_NODE_CAP`` nodes are returned.  If the project
          exceeds this, ``capped=True`` is set in the envelope and a warning is
          logged.  No silent truncation — the cap is documented in the response.
        """
        import logging as _logging
        _log = _logging.getLogger("misaka.core.versioning")

        _, project_dir = self.project_manager.get_project(project_id)
        assets = self._read_assets(project_dir)

        # Build a fast lookup by asset id.
        asset_by_id: dict[str, object] = {a.id: a for a in assets}
        known_ids: set[str] = set(asset_by_id)

        # Cycle detection: walk ancestor chains, record every id visited within
        # that chain.  If we encounter an id already in the current chain path
        # we have a cycle.
        cycle_detected = False

        def _has_cycle(start_id: str) -> bool:
            """Return True if following parent_version_id from start_id hits a cycle."""
            visited: set[str] = set()
            current_id: str | None = start_id
            while current_id is not None:
                if current_id in visited:
                    return True
                visited.add(current_id)
                asset = asset_by_id.get(current_id)
                if asset is None:
                    break
                current_id = asset.parent_version_id  # type: ignore[attr-defined]
            return False

        nodes: list[VersionTreeNode] = []
        for asset in assets:
            if len(nodes) >= self._TREE_NODE_CAP:
                _log.warning(
                    "build_version_tree: node cap %d reached for project %s — %d assets truncated",
                    self._TREE_NODE_CAP,
                    project_id,
                    len(assets) - len(nodes),
                )
                break

            parent_id: str | None = getattr(asset, "parent_version_id", None)
            is_orphaned = parent_id is not None and parent_id not in known_ids

            # Check for cycles starting from this node's parent chain.
            if parent_id is not None and _has_cycle(parent_id):
                cycle_detected = True
                # Break the cycle by treating this node as a root to avoid
                # following the malformed chain.
                parent_id = None

            nodes.append(
                VersionTreeNode(
                    id=asset.id,
                    parent_id=parent_id,
                    asset_type=asset.asset_type,
                    modality=asset.modality,
                    title=asset.title,
                    status=asset.asset_type,
                    created_at=asset.created_at,
                    prompt_hash=getattr(asset, "prompt_hash", None),
                    refine_strategy=getattr(asset, "refine_strategy", None),
                    prompt_delta=getattr(asset, "prompt_delta", None),
                    param_delta=dict(getattr(asset, "param_delta", None) or {}),
                    mask_asset_id=getattr(asset, "mask_asset_id", None),
                    backend=getattr(asset, "backend", None),
                    is_orphaned=is_orphaned,
                )
            )

        capped = len(assets) > self._TREE_NODE_CAP
        nodes.sort(key=lambda n: n.created_at)
        return VersionTreeData(
            nodes=nodes,
            cycle_detected=cycle_detected,
            capped=capped,
            node_cap=self._TREE_NODE_CAP,
        )

    # ------------------------------------------------------------------
    # M5.1 — Version Diff  (spec §8.2)
    # ------------------------------------------------------------------

    def diff_versions(self, project_id: str, from_id: str, to_id: str) -> VersionDiffData:
        """Compute a structured delta between two asset versions (spec §8.2 / M5.1).

        Pure / deterministic: no I/O beyond reading the asset index.
        Returns a ``VersionDiffData`` whose fields are set only when the two
        versions actually differ.  Both ``from_id`` and ``to_id`` must exist in
        the project; raises ``FileNotFoundError`` otherwise.
        """
        _, project_dir = self.project_manager.get_project(project_id)
        assets = self._read_assets(project_dir)
        asset_by_id: dict[str, object] = {a.id: a for a in assets}

        from_asset = asset_by_id.get(from_id)
        to_asset = asset_by_id.get(to_id)
        if from_asset is None:
            raise FileNotFoundError(f"Version not found: {from_id}")
        if to_asset is None:
            raise FileNotFoundError(f"Version not found: {to_id}")

        # --- prompt delta ---
        # Use the recorded prompt_delta on the ``to`` node when available
        # (set during refine-accept, spec §5.11).  Fall back to comparing
        # prompt_hash values to surface a synthetic "hashes differ" note.
        to_prompt_delta: str | None = getattr(to_asset, "prompt_delta", None)
        from_hash = getattr(from_asset, "prompt_hash", None)
        to_hash = getattr(to_asset, "prompt_hash", None)
        if not to_prompt_delta and from_hash != to_hash and (from_hash or to_hash):
            to_prompt_delta = f"prompt_hash: {from_hash or '(none)'} → {to_hash or '(none)'}"

        # --- param delta ---
        from_params: dict = dict(getattr(from_asset, "params", None) or {})
        to_params: dict = dict(getattr(to_asset, "params", None) or {})
        # Only include keys that differ or are new in ``to``.
        param_delta: dict = {
            k: v for k, v in to_params.items()
            if k not in from_params or from_params[k] != v
        }
        # If ``to`` has a recorded param_delta, prefer it (more authoritative).
        recorded_param_delta: dict = dict(getattr(to_asset, "param_delta", None) or {})
        if recorded_param_delta:
            param_delta = recorded_param_delta

        # --- mask diff ---
        from_mask = getattr(from_asset, "mask_asset_id", None)
        to_mask = getattr(to_asset, "mask_asset_id", None)
        mask_diff: dict | None = None
        if from_mask != to_mask:
            mask_diff = {"from_mask": from_mask, "to_mask": to_mask}

        # --- recipe diff ---
        from_strategy = getattr(from_asset, "refine_strategy", None)
        to_strategy = getattr(to_asset, "refine_strategy", None)
        strategy_diff: dict | None = None
        if from_strategy != to_strategy:
            strategy_diff = {
                "from": from_strategy.value if from_strategy else None,
                "to": to_strategy.value if to_strategy else None,
            }

        # recipe_diff mirrors strategy_diff in this model (both derived from
        # refine_strategy; a richer multi-field recipe comparison is left for
        # when a dedicated ``recipe`` field is added to AssetRecord).
        recipe_diff: dict | None = strategy_diff

        # --- backend diff ---
        from_backend = getattr(from_asset, "backend", None)
        to_backend = getattr(to_asset, "backend", None)
        backend_diff: dict | None = None
        if from_backend != to_backend:
            backend_diff = {"from": from_backend, "to": to_backend}

        return VersionDiffData(
            from_id=from_id,
            to_id=to_id,
            prompt_delta=to_prompt_delta or None,
            param_delta=param_delta,
            mask_diff=mask_diff,
            recipe_diff=recipe_diff,
            strategy_diff=strategy_diff,
            backend_diff=backend_diff,
        )

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
            # Parent-child lineage (spec §5.11 / §8.1). On a refine job the
            # produced asset points back at the parent version and records the
            # strategy, mask source, prompt delta and param delta it used.
            parent_version_id=job.parent_asset_id if job else None,
            refine_strategy=job.refine_strategy if job else None,
            mask_asset_id=job.mask_asset_id if job else None,
            prompt_delta=job.prompt_delta if job else None,
            param_delta=dict(job.param_delta) if job else {},
            backend=job.worker if job else None,
            params=dict(job.params) if job else {},
            prompt_hash=_prompt_hash(job.prompt) if job else None,
            created_at=datetime.now(timezone.utc),
        )

    def _training_lock_blocking_reason(self) -> str | None:
        """Return the training-lock blocking reason if the VRAM lock is held, else None.

        Reuses the existing blocking-reason pattern (spec §7.3 / M3 batch-blocking
        mechanism) so no new error surface is introduced at the API layer.
        """
        if self._scheduler is not None and self._scheduler.is_training_locked():
            return _TRAINING_LOCK_REASON
        return None

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
        # M3(a) live-first: if the worker is already running and readiness_note
        # is None the worker is live-usable regardless of local install state.
        # The is_installed / is_running fallbacks only apply when the worker is
        # NOT running (i.e. we cannot reach it at all).
        if snapshot.is_running:
            return None
        if not snapshot.is_installed:
            return f"{snapshot.display_name} is not installed yet."
        return f"{snapshot.display_name} is installed but not running."

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
                "training in progress",
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
        jobs: list[GenerationJob] = []
        skipped = 0
        for item in payload.get("jobs", []):
            # Legacy jobs written before asset_type was added carry only modality.
            # Derive asset_type from modality so these records stay loadable.
            if "asset_type" not in item and "modality" in item:
                item = {**item, "asset_type": item["modality"]}
                logger.warning("legacy job upgraded: derived asset_type=%r from modality", item["asset_type"])
            try:
                jobs.append(GenerationJob(**item))
            except Exception:
                skipped += 1
        if skipped:
            logger.warning("_read_jobs: skipped %d malformed record(s) in %s", skipped, path)
        return jobs

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

    @staticmethod
    def _extract_metadata_delta(instruction: str) -> dict[str, object]:
        """Parse a metadata-only refine instruction for tag, note and favorite signals.

        Returns a dict with optional keys:
          ``tags``        – list[str] of hashtag tokens found in the instruction
          ``is_favorite`` – True if the instruction contains a favorite signal
          ``user_note``   – the raw instruction text, used as a freeform note
        """
        import re as _re

        lowered = instruction.lower()
        delta: dict[str, object] = {}

        # Collect #tag tokens (ASCII or CJK, allowing hyphens/underscores).
        tags: list[str] = _re.findall(r"#([\w一-鿿\-_]+)", instruction)
        if tags:
            delta["tags"] = tags

        # Favorite signal detection (zh-TW + en keywords).
        favorite_keywords = ("最愛", "favorite", "favourite", "收藏")
        if any(k in lowered for k in favorite_keywords):
            delta["is_favorite"] = True

        # Store instruction as a free-form note so the edit intent is preserved.
        delta["user_note"] = instruction.strip()

        return delta
