"""Fidelity refine-loop orchestration service (spec §5.15 / C-spec.md §4-6).

Wires the pure controller (``core.consultant.fidelity_loop``) to real I/O:
loads a character's SSOT checklist, runs the VLM critic against an asset's
bytes, builds a mask via the SAME code path ``POST .../assets/{id}/mask``
uses (``core.editor.mask`` + ``GenerationService.import_asset`` — never an
HTTP call to self), dispatches a refine job via
``GenerationService.refine_asset`` + ``execute_job``, and persists every
round via ``core.consultant.fidelity_store.FidelityStore``.

Every I/O step is behind an injectable callable (``critique_fn``,
``mask_builder_fn``, ``refine_fn``) defaulting to the real implementation —
tests inject fakes so no Ollama/ComfyUI call ever happens under pytest
(spec brief: "Fakes injected — no Ollama/ComfyUI in tests").
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path

from core.config import Settings, get_settings
from core.consultant import fidelity_loop
from core.consultant.fidelity import load_character_sources, parse_character_checklist
from core.consultant.fidelity_store import FidelityStore
from core.editor.mask import build_mask_png, read_image_size
from core.generation.service import GenerationService
from core.llm import vision
from core.models.schemas import (
    CharacterSheet,
    FidelityCheck,
    FidelityCheckResult,
    FidelityLoop,
    FidelityLoopData,
    FidelityLoopRound,
    FidelityLoopStartRequest,
    FidelityLoopStatus,
    FidelityRoundPlan,
    GenerationJobStatus,
    MaskFromRegionsRequest,
    Modality,
    RefineRequest,
)
from core.network.state import NetworkState
from core.project.manager import ProjectManager

_TERMINAL_LOOP_STATUSES = frozenset(
    {FidelityLoopStatus.PASSED, FidelityLoopStatus.STOPPED_MAX_ROUNDS, FidelityLoopStatus.FAILED}
)
# A loop paused here is still awaiting exactly one more round (either a
# user's explicit advance() click, or auto_continue picking it back up).
_AWAITING_ROUND_STATUSES = frozenset(
    {FidelityLoopStatus.AWAITING_USER, FidelityLoopStatus.STOPPED_REGRESSION_RECOVERED}
)

CritiqueFn = Callable[[bytes, list[FidelityCheck], int, int], list[FidelityCheckResult]]
MaskBuilderFn = Callable[[str, str, FidelityRoundPlan], str]
RefineFn = Callable[[str, str, RefineRequest], tuple[str, str]]
CharacterSheetResolver = Callable[[str, str], CharacterSheet | None]
FidelityStoreResolver = Callable[[str], FidelityStore]


class FidelityLoopConflictError(RuntimeError):
    """Raised when a fidelity-loop round transition could not be claimed
    because another request already changed (or is concurrently changing)
    the loop's status (C2-review.md MAJOR #1). Mapped to HTTP 409 by
    ``core/main.py`` — distinct from the plain 400 ``ValueError`` raised when
    the loop was ALREADY known (at call time) not to be awaiting a round."""


class FidelityService:
    def __init__(
        self,
        project_manager: ProjectManager,
        generation_service: GenerationService,
        fidelity_store_resolver: FidelityStoreResolver,
        character_sheet_resolver: CharacterSheetResolver,
        *,
        settings: Settings | None = None,
        network_state_provider: Callable[[], NetworkState] | None = None,
        critique_fn: CritiqueFn | None = None,
        mask_builder_fn: MaskBuilderFn | None = None,
        refine_fn: RefineFn | None = None,
    ) -> None:
        self.project_manager = project_manager
        self.generation_service = generation_service
        self._fidelity_store_resolver = fidelity_store_resolver
        self._character_sheet_resolver = character_sheet_resolver
        self._settings = settings or get_settings()
        self._network_state_provider = network_state_provider or (lambda: NetworkState.OFFLINE)
        self._critique_fn = critique_fn or self._default_critique
        self._mask_builder_fn = mask_builder_fn or self._default_build_mask
        self._refine_fn = refine_fn or self._default_refine
        # Per-loop in-process mutual exclusion for advance()'s round body
        # (C2-review.md MAJOR #1), on top of the atomic DB claim below — two
        # threads in the SAME process must never both be inside the
        # mask/refine/critique body for one loop, even before either reaches
        # the database. ``_loop_locks_guard`` protects creating a NEW Lock
        # for a not-yet-seen loop_id; the per-loop Lock itself protects the
        # round body once acquired.
        self._loop_locks: dict[str, threading.Lock] = {}
        self._loop_locks_guard = threading.Lock()

    def _get_loop_lock(self, loop_id: str) -> threading.Lock:
        with self._loop_locks_guard:
            lock = self._loop_locks.get(loop_id)
            if lock is None:
                lock = threading.Lock()
                self._loop_locks[loop_id] = lock
            return lock

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start_loop(self, project_id: str, asset_id: str, request: FidelityLoopStartRequest) -> FidelityLoopData:
        """Create a loop and run round 0 — the baseline critique of the
        ROOT asset, no mask/refine yet (spec §4.1: "PENDING_CRITIQUE(round=0,
        target=root) -> CRITIQUING"). Starting the loop IS the round-0
        "click" (spec §4.3: "啟動永遠一張卡(必須點擊 POST .../fidelity-loop)")
        — a separate advance() call is never needed for round 0."""
        _, project_dir = self.project_manager.get_project(project_id)
        sheet = self._resolve_character_sheet(project_id, request.character_sheet_id)
        checks = self._load_checks(sheet, request.outfit_variant)
        # Validate the root asset exists/is an image BEFORE creating any row.
        self._read_asset_image(project_dir, asset_id)

        store = self._fidelity_store_resolver(project_id)
        loop = store.create_loop(
            project_id=project_id,
            root_asset_id=asset_id,
            character_sheet_id=request.character_sheet_id,
            outfit_variant=request.outfit_variant,
            max_rounds=request.max_rounds,
            auto_continue=request.auto_continue,
        )

        self._run_baseline_critique(project_dir, store, loop, checks)
        if loop.auto_continue:
            self._run_auto_continue(project_dir, store, loop, checks)

        return self._to_data(store, loop, checks)

    def advance(self, project_id: str, loop_id: str) -> FidelityLoopData:
        """Run EXACTLY one refine round (spec §4.3: "advance() 執行下一輪").

        Concurrency guard (C2-review.md MAJOR #1): a plain check-then-act on
        ``loop.status`` lets two simultaneous callers both pass the guard
        before either writes back, running the same round twice and
        clobbering each other's state. Fixed with two layers: (1) a
        non-blocking per-loop in-process lock, so a second concurrent call in
        THIS process fails fast instead of racing; (2) underneath it, an
        atomic single-statement DB claim (``FidelityStore.claim_round``,
        ``UPDATE ... WHERE status IN (...)``) that only ONE caller can win
        even across processes/connections. Either layer failing raises
        :class:`FidelityLoopConflictError` (409), never runs the round twice.
        """
        _, project_dir = self.project_manager.get_project(project_id)
        store = self._fidelity_store_resolver(project_id)
        loop = self._require_loop(store, project_id, loop_id)
        if loop.status not in _AWAITING_ROUND_STATUSES:
            raise ValueError(
                f"Loop {loop_id} is not awaiting a round (status={loop.status.value!r})."
            )
        lock = self._get_loop_lock(loop_id)
        if not lock.acquire(blocking=False):
            raise FidelityLoopConflictError(
                f"Loop {loop_id} already has a round in progress."
            )
        try:
            if not store.claim_round(
                project_id, loop_id, _AWAITING_ROUND_STATUSES, FidelityLoopStatus.BUILDING_MASK
            ):
                raise FidelityLoopConflictError(
                    f"Loop {loop_id} is not awaiting a round (status changed concurrently)."
                )
            sheet = self._resolve_character_sheet(project_id, loop.character_sheet_id)
            checks = self._load_checks(sheet, loop.outfit_variant)
            self._run_refine_round(project_dir, store, loop, checks)
            return self._to_data(store, loop, checks)
        finally:
            lock.release()

    def get(self, project_id: str, loop_id: str) -> FidelityLoopData:
        store = self._fidelity_store_resolver(project_id)
        loop = self._require_loop(store, project_id, loop_id)
        sheet = self._resolve_character_sheet(project_id, loop.character_sheet_id)
        checks = self._load_checks(sheet, loop.outfit_variant)
        return self._to_data(store, loop, checks)

    def stream_loop_progress(
        self,
        project_id: str,
        loop_id: str,
        *,
        poll_interval_sec: float = 1.0,
        max_duration_sec: float = 24 * 60 * 60.0,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> Iterator[FidelityLoop]:
        """Yield a ``FidelityLoop`` snapshot whenever ``(status, current_round,
        best_pass_count)`` changes (spec §4.4, mirrors
        ``TrainingService.stream_job_progress``, core/training/service.py:109-158).
        Stops once the loop reaches a terminal status (PASSED /
        STOPPED_MAX_ROUNDS / FAILED) — ``AWAITING_USER`` /
        ``STOPPED_REGRESSION_RECOVERED`` are NOT terminal here: more rounds
        may still follow from another request calling ``advance()``."""
        store = self._fidelity_store_resolver(project_id)
        deadline = clock() + max_duration_sec
        last_signature: tuple[FidelityLoopStatus, int, int] | None = None
        first = True
        while True:
            loop = store.get_loop(project_id, loop_id)
            if loop is None:
                return
            signature = (loop.status, loop.current_round, loop.best_pass_count)
            if first or signature != last_signature:
                yield loop
                last_signature = signature
                first = False
            if loop.status in _TERMINAL_LOOP_STATUSES:
                return
            if clock() >= deadline:
                return
            sleep(poll_interval_sec)

    # ------------------------------------------------------------------
    # Round execution
    # ------------------------------------------------------------------

    def _run_baseline_critique(
        self, project_dir: Path, store: FidelityStore, loop: FidelityLoop, checks: list[FidelityCheck]
    ) -> None:
        """Round-0 baseline critique (spec §4.1). Wrapped symmetrically with
        ``_run_refine_round`` below (C2-review.md MAJOR #2): before this fix,
        an exception here left the loop stuck in ``PENDING_CRITIQUE``
        forever — a status ``advance()`` never accepts (only
        ``_AWAITING_ROUND_STATUSES``), so the loop was permanently
        unrecoverable. Any exception now sets ``FAILED`` + persists the
        message to ``last_error`` (surfaced by ``GET``) before re-raising,
        exactly like a refine-round failure."""
        try:
            image_bytes, width, height = self._read_asset_image(project_dir, loop.root_asset_id)
            critic_results = self._critique_fn(image_bytes, checks, width, height)
        except Exception as error:
            loop.status = FidelityLoopStatus.FAILED
            loop.last_error = str(error)
            store.save_loop(loop)
            raise
        pass_count, fail_count = fidelity_loop.summarize_pass_fail(critic_results)
        store.append_round(
            loop_id=loop.id,
            round_index=0,
            asset_id=loop.root_asset_id,
            critic_results=critic_results,
            pass_count=pass_count,
            fail_count=fail_count,
        )
        outcome = fidelity_loop.decide_round_outcome(
            new_asset_id=loop.root_asset_id,
            new_pass_count=pass_count,
            new_fail_count=fail_count,
            completed_round_index=0,
            max_rounds=loop.max_rounds,
            best_asset_id=loop.best_asset_id,
            best_pass_count=fidelity_loop.INITIAL_BEST_PASS_COUNT,
        )
        self._apply_outcome(store, loop, outcome, round_index=0)

    def _run_refine_round(
        self, project_dir: Path, store: FidelityStore, loop: FidelityLoop, checks: list[FidelityCheck]
    ) -> None:
        latest_round = store.latest_round(loop.id)
        if latest_round is None:
            raise RuntimeError(f"Loop {loop.id} has no rounds recorded yet.")

        if loop.status is FidelityLoopStatus.STOPPED_REGRESSION_RECOVERED:
            # Re-target the BEST asset's own critic results, never the
            # regressed round's (spec §4.1: "下一輪 target = best_so_far").
            target_asset_id = loop.best_asset_id
            source_round = self._find_round_for_asset(store, loop.id, target_asset_id) or latest_round
        else:
            target_asset_id = latest_round.asset_id
            source_round = latest_round

        _, width, height = self._read_asset_image(project_dir, target_asset_id)
        round_index = loop.current_round + 1
        plan = fidelity_loop.plan_round(
            round_index=round_index,
            target_asset_id=target_asset_id,
            checks=checks,
            critic_results=source_round.critic_results,
            image_width=width,
            image_height=height,
        )
        if plan is None:
            # No fails recorded to plan from — treat as already passed
            # rather than raising (should not normally be reachable, since
            # AWAITING_USER/STOPPED_REGRESSION_RECOVERED implies fails).
            loop.status = FidelityLoopStatus.PASSED
            store.save_loop(loop)
            return

        try:
            loop.status = FidelityLoopStatus.BUILDING_MASK
            store.save_loop(loop)
            mask_asset_id = self._mask_builder_fn(loop.project_id, target_asset_id, plan)

            loop.status = FidelityLoopStatus.REFINING
            store.save_loop(loop)
            refine_request = fidelity_loop.build_refine_request(plan, mask_asset_id)
            new_asset_id, refine_job_id = self._refine_fn(loop.project_id, target_asset_id, refine_request)

            loop.status = FidelityLoopStatus.CRITIQUING
            store.save_loop(loop)
            image_bytes, new_width, new_height = self._read_asset_image(project_dir, new_asset_id)
            critic_results = self._critique_fn(image_bytes, checks, new_width, new_height)
        except Exception as error:
            loop.status = FidelityLoopStatus.FAILED
            loop.last_error = str(error)
            store.save_loop(loop)
            raise

        pass_count, fail_count = fidelity_loop.summarize_pass_fail(critic_results)
        store.append_round(
            loop_id=loop.id,
            round_index=round_index,
            asset_id=new_asset_id,
            critic_results=critic_results,
            pass_count=pass_count,
            fail_count=fail_count,
            mask_asset_id=mask_asset_id,
            refine_job_id=refine_job_id,
        )
        outcome = fidelity_loop.decide_round_outcome(
            new_asset_id=new_asset_id,
            new_pass_count=pass_count,
            new_fail_count=fail_count,
            completed_round_index=round_index,
            max_rounds=loop.max_rounds,
            best_asset_id=loop.best_asset_id,
            best_pass_count=loop.best_pass_count,
        )
        self._apply_outcome(store, loop, outcome, round_index=round_index)

    def _run_auto_continue(
        self, project_dir: Path, store: FidelityStore, loop: FidelityLoop, checks: list[FidelityCheck]
    ) -> None:
        # Bounded by max_rounds+1 so a controller defect can never spin
        # forever even if it kept returning a non-terminal status.
        guard = 0
        while loop.status in _AWAITING_ROUND_STATUSES and guard <= loop.max_rounds:
            guard += 1
            self._run_refine_round(project_dir, store, loop, checks)

    @staticmethod
    def _apply_outcome(store: FidelityStore, loop: FidelityLoop, outcome: fidelity_loop.RoundOutcome, *, round_index: int) -> None:
        loop.status = outcome.status
        loop.current_round = round_index
        loop.best_asset_id = outcome.best_asset_id
        loop.best_pass_count = outcome.best_pass_count
        store.save_loop(loop)

    @staticmethod
    def _find_round_for_asset(store: FidelityStore, loop_id: str, asset_id: str) -> FidelityLoopRound | None:
        for round_record in reversed(store.list_rounds(loop_id)):
            if round_record.asset_id == asset_id:
                return round_record
        return None

    # ------------------------------------------------------------------
    # Response assembly
    # ------------------------------------------------------------------

    def _to_data(self, store: FidelityStore, loop: FidelityLoop, checks: list[FidelityCheck]) -> FidelityLoopData:
        rounds = store.list_rounds(loop.id)
        latest = rounds[-1] if rounds else None
        unresolved_ids = [result.id for result in latest.critic_results if not result.passed] if latest else []

        next_plan: FidelityRoundPlan | None = None
        if latest is not None and loop.status in _AWAITING_ROUND_STATUSES:
            next_plan = self._preview_next_plan(loop, store, checks, latest)

        return FidelityLoopData(loop=loop, rounds=rounds, unresolved_check_ids=unresolved_ids, next_round_plan=next_plan)

    def _preview_next_plan(
        self, loop: FidelityLoop, store: FidelityStore, checks: list[FidelityCheck], latest: FidelityLoopRound
    ) -> FidelityRoundPlan | None:
        """Compute (without executing) the SAME plan ``advance()`` would act
        on next — deterministically re-derivable from already-persisted
        critic_json, so this is never itself a separate persisted row (see
        ``FidelityRoundPlan`` docstring). Best-effort: a failure here (e.g.
        the target asset file went missing on disk) must never break a GET,
        so it degrades to ``None`` instead of raising."""
        if loop.status is FidelityLoopStatus.STOPPED_REGRESSION_RECOVERED:
            source_round = self._find_round_for_asset(store, loop.id, loop.best_asset_id) or latest
            target_asset_id = loop.best_asset_id
        else:
            source_round = latest
            target_asset_id = latest.asset_id
        try:
            _, project_dir = self.project_manager.get_project(loop.project_id)
            _, width, height = self._read_asset_image(project_dir, target_asset_id)
            return fidelity_loop.plan_round(
                round_index=loop.current_round + 1,
                target_asset_id=target_asset_id,
                checks=checks,
                critic_results=source_round.critic_results,
                image_width=width,
                image_height=height,
            )
        except Exception:
            return None

    # ------------------------------------------------------------------
    # Lookups / validation
    # ------------------------------------------------------------------

    def _resolve_character_sheet(self, project_id: str, character_sheet_id: str) -> CharacterSheet:
        sheet = self._character_sheet_resolver(project_id, character_sheet_id)
        if sheet is None:
            raise ValueError(f"Unknown character_sheet_id: {character_sheet_id!r}")
        return sheet

    @staticmethod
    def _load_checks(sheet: CharacterSheet, outfit_variant: str) -> list[FidelityCheck]:
        if not sheet.sheet_source_path:
            raise ValueError(
                f"Character sheet {sheet.id!r} has no sheet_source_path configured "
                "— cannot derive a fidelity checklist."
            )
        setting_md, outfits_md = load_character_sources(sheet.sheet_source_path)
        # parse_character_checklist itself raises ValueError, listing every
        # available variant, on an unknown outfit_variant (core/consultant/
        # fidelity.py) — propagated as-is, mapped to HTTP 400 by main.py.
        return parse_character_checklist(setting_md, outfits_md, outfit_variant)

    def _require_loop(self, store: FidelityStore, project_id: str, loop_id: str) -> FidelityLoop:
        loop = store.get_loop(project_id, loop_id)
        if loop is None:
            raise FileNotFoundError(f"Fidelity loop not found: {loop_id}")
        return loop

    def _read_asset_image(self, project_dir: Path, asset_id: str) -> tuple[bytes, int, int]:
        """Read an asset's raw bytes + pixel dimensions, applying the SAME
        resolve-then-contain guard as ``GET .../assets/{id}/file`` and
        ``POST .../assets/{id}/mask`` (core/main.py) — duplicated here
        rather than refactored into a shared helper to keep this a minimal,
        additive diff (the two existing routes already carry the identical
        duplicated check)."""
        assets = self.generation_service._read_assets(project_dir)
        asset = next((item for item in assets if item.id == asset_id), None)
        if asset is None:
            raise FileNotFoundError(f"Asset not found: {asset_id}")
        if asset.modality is not Modality.IMAGE:
            raise ValueError(f"Asset {asset_id} must be an image, got modality={asset.modality.value!r}")

        assets_root = (project_dir / "assets").resolve()
        file_path = (project_dir / asset.path).resolve()
        try:
            file_path.relative_to(assets_root)
        except ValueError as error:
            raise ValueError("Asset file path is outside the permitted directory.") from error
        if not file_path.exists():
            raise FileNotFoundError(f"Asset file not found on disk: {asset.path}")

        content = file_path.read_bytes()
        width, height = read_image_size(content)
        return content, width, height

    # ------------------------------------------------------------------
    # Default (real) I/O implementations — injectable for tests
    # ------------------------------------------------------------------

    def _default_critique(
        self, image_bytes: bytes, checks: list[FidelityCheck], width: int, height: int
    ) -> list[FidelityCheckResult]:
        return vision.critique(
            self._settings, image_bytes, checks, width, height, self._network_state_provider()
        )

    def _default_build_mask(self, project_id: str, target_asset_id: str, plan: FidelityRoundPlan) -> str:
        """Build the mask asset via the SAME code path ``POST .../mask``
        uses — ``core.editor.mask.build_mask_png`` + ``GenerationService.
        import_asset`` — never an HTTP call to self (spec brief)."""
        _, project_dir = self.project_manager.get_project(project_id)
        _, width, height = self._read_asset_image(project_dir, target_asset_id)
        mask_request = MaskFromRegionsRequest(regions=plan.mask_regions, subtract=plan.mask_subtract)
        result = build_mask_png(width, height, mask_request)
        workspace = self.generation_service.import_asset(
            project_id,
            filename=f"fidelity-mask-round{plan.round_index}.png",
            content=result.png_bytes,
            modality=Modality.IMAGE,
            asset_type="mask",
            title=f"fidelity-mask-round{plan.round_index}",
            description=f"Auto-generated fidelity-loop mask for round {plan.round_index}",
        )
        return workspace.assets[-1].id

    def _default_refine(self, project_id: str, target_asset_id: str, refine_request: RefineRequest) -> tuple[str, str]:
        workspace = self.generation_service.refine_asset(project_id, target_asset_id, refine_request)
        job = workspace.jobs[-1]
        if job.status == GenerationJobStatus.BLOCKED:
            raise ValueError(job.blocking_reason or f"Refine job {job.id} is blocked.")

        workspace = self.generation_service.execute_job(project_id, job.id)
        updated_job = next((item for item in workspace.jobs if item.id == job.id), job)
        if updated_job.status == GenerationJobStatus.FAILED:
            raise ValueError(updated_job.last_error or f"Refine job {job.id} failed.")

        created_assets = [asset for asset in workspace.assets if asset.job_id == job.id]
        if not created_assets:
            raise RuntimeError(f"Refine job {job.id} completed but produced no asset.")
        return created_assets[-1].id, job.id
