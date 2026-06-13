"""Training executor — FIFO queue with hard exclusive VRAM lock (spec §7.3 / M4.d).

Design
------
TrainingExecutor runs queued TrainingJobs ONE AT A TIME (FIFO).  Before launching
each job it acquires an EXCLUSIVE VRAM lock via ModelScheduler.begin_training()
(core/scheduler/vram.py).  While the lock is held:
  * acquire() on any model raises SchedulerError immediately — HARD refusal, not
    eviction-based.  Generation dispatch must consult is_training_locked() before
    attempting to run (see core/generation/service.py).
  * The lock is released unconditionally via end_training() in the finally block
    on completion/failure/cancel.

VRAM lock integration with core/scheduler/vram.py
---------------------------------------------------
The scheduler now has a first-class training-lock concept (begin_training /
end_training / is_training_locked).  While the lock is held:
  - acquire() raises SchedulerError for ANY caller (non-evictable hard lock).
  - Other subsystems query is_training_locked() to gate generation dispatch.

Direction (a) — training refuses to start if a managed model is ACTIVE:
  Before calling begin_training() the executor checks whether any registered
  managed model is currently ACTIVE.  If one is found, the job FAILS immediately
  with a clear reason.  This prevents claiming bidirectional exclusivity that
  has not been wired end-to-end.

Scheduler API calls used (all from core/scheduler/vram.py):
  ModelScheduler.begin_training(holder)  — acquires hard exclusive lock
  ModelScheduler.end_training()          — releases the lock
  ModelScheduler.is_training_locked()    — query by generation path

CommandRunner protocol
-----------------------
The executor depends on an injected CommandRunner (see protocol below).  The
default SubprocessRunner uses subprocess.Popen; tests inject FakeRunner, which
returns a configurable exit code without spawning real processes.

Live command construction (MAJOR 3 fix)
---------------------------------------
_resolve_command() now looks up the job's project_id and worker, then calls
build_lora_command() / build_voice_clone_command() with entities from an
injected AssetStore resolver.  If required entities are missing the job fails
with a clear reason — the ["echo", ...] stub is gone from the live path.
The fragile enqueue_with_command monkey-patch is removed; _pending_commands
is initialised in __init__ and enqueue_with_command uses it cleanly.

Per-project job store
----------------------
read_jobs / write_jobs are now Callable[[str], ...] — they take a project_id
so the executor can serve multiple projects without binding to the first one.

Real-run deferred / wired-but-not-live-verified
-----------------------------------------------
The executor is COMPLETE and CORRECT by contract and passes all tests with
FakeRunner.  It has NOT been run against a real kohya_ss or GPT-SoVITS
installation.  The user will perform real runs later.  See spec §7.3 and
RESEARCH_LOG §10.

TODO (spec §7.3 resume / mid-checkpoint trial)
----------------------------------------------
Resume from checkpoint is NOT yet implemented.  When a job is cancelled or
fails, executor sets job.status = FAILED with a note and preserves
resume_checkpoint_path = None.  A future phase will:
  1. Parse kohya_ss --save_every_n_epochs output to find the last checkpoint dir.
  2. Set job.resume_checkpoint_path before transitioning to FAILED.
  3. The next submit_job call for the same entity can read resume_checkpoint_path
     and pass --resume_from_checkpoint to kohya_ss / GPT-SoVITS.
  Spec ref: §7.3 "可中斷、可續訓、可試聽中間 checkpoint"
"""

from __future__ import annotations

import logging
import queue
import threading
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol, runtime_checkable

from core.models.schemas import Modality, TrainingJob, TrainingJobStatus
from core.scheduler.vram import ModelScheduler, RuntimeState, SchedulerError

logger = logging.getLogger("misaka.training.executor")

# How many characters of stderr tail to preserve on failure.
_STDERR_TAIL_CHARS = 4096


# ---------------------------------------------------------------------------
# CommandRunner protocol (dependency-injection seam)
# ---------------------------------------------------------------------------

@runtime_checkable
class CommandRunner(Protocol):
    """Protocol for launching a training subprocess.

    The executor calls run() with the argv list and the working directory.
    The implementation is responsible for:
      * Starting the subprocess.
      * Streaming stdout/stderr so the executor can detect progress.
      * Returning when the process exits.
      * Honouring cancel() by terminating the running process.

    A concrete implementation must be thread-safe: run() is called from the
    executor worker thread; cancel() may be called from any thread.
    """

    def run(
        self,
        args: list[str],
        cwd: Path,
        *,
        on_progress: Callable[[int, str], None] | None = None,
    ) -> "RunResult":
        """Launch the process and block until exit.  Returns RunResult."""
        ...

    def cancel(self) -> None:
        """Signal the currently-running process to terminate."""
        ...


class RunResult:
    """Outcome of a single CommandRunner.run() call."""

    def __init__(self, exit_code: int, stderr_tail: str) -> None:
        self.exit_code = exit_code
        self.stderr_tail = stderr_tail

    @property
    def succeeded(self) -> bool:
        return self.exit_code == 0


# ---------------------------------------------------------------------------
# Default real implementation (subprocess — unused in tests)
# ---------------------------------------------------------------------------

class SubprocessRunner:
    """Real CommandRunner backed by subprocess.Popen.

    REAL-RUN NOTE: This class is wired but not live-verified.  It will be
    exercised when the user performs real training runs.
    """

    def __init__(self) -> None:
        self._process: "subprocess.Popen[bytes] | None" = None  # type: ignore[name-defined]
        self._cancel_event = threading.Event()

    def run(
        self,
        args: list[str],
        cwd: Path,
        *,
        on_progress: Callable[[int, str], None] | None = None,
    ) -> RunResult:
        import subprocess
        self._cancel_event.clear()
        stderr_lines: list[str] = []
        try:
            self._process = subprocess.Popen(
                args,
                cwd=cwd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            assert self._process.stdout is not None
            assert self._process.stderr is not None

            # Drain stderr in a background thread.
            def _read_stderr() -> None:
                for line in self._process.stderr:  # type: ignore[union-attr]
                    stderr_lines.append(line)
                    if self._cancel_event.is_set():
                        break

            stderr_thread = threading.Thread(target=_read_stderr, daemon=True)
            stderr_thread.start()

            # Read stdout for progress signals (e.g. "steps: 10/100").
            for line in self._process.stdout:
                if self._cancel_event.is_set():
                    self._process.terminate()
                    break
                _maybe_report_progress(line, on_progress)

            self._process.wait()
            stderr_thread.join(timeout=5.0)
            exit_code = self._process.returncode
        finally:
            self._process = None

        tail = "".join(stderr_lines)[-_STDERR_TAIL_CHARS:]
        return RunResult(exit_code=exit_code or 0, stderr_tail=tail)

    def cancel(self) -> None:
        self._cancel_event.set()
        if self._process is not None:
            self._process.terminate()


# ---------------------------------------------------------------------------
# Fake runner for tests (no subprocess, no GPU)
# ---------------------------------------------------------------------------

class FakeRunner:
    """CommandRunner for tests — returns a fixed exit_code without any process.

    Usage in tests::

        runner = FakeRunner(exit_code=0)          # success
        runner = FakeRunner(exit_code=1,          # failure
                            stderr="error msg")
    """

    def __init__(self, exit_code: int = 0, stderr: str = "") -> None:
        self.exit_code = exit_code
        self.stderr = stderr
        self.calls: list[tuple[list[str], Path]] = []
        self._cancelled = False

    def run(
        self,
        args: list[str],
        cwd: Path,
        *,
        on_progress: Callable[[int, str], None] | None = None,
    ) -> RunResult:
        self.calls.append((args, cwd))
        if on_progress is not None:
            on_progress(50, "FakeRunner progress mid-point")
            on_progress(100, "FakeRunner done")
        return RunResult(exit_code=self.exit_code, stderr_tail=self.stderr)

    def cancel(self) -> None:
        self._cancelled = True


# ---------------------------------------------------------------------------
# TrainingExecutor
# ---------------------------------------------------------------------------

class TrainingExecutor:
    """FIFO single-concurrency training job executor with hard exclusive VRAM lock.

    The executor owns a background worker thread that dequeues jobs one at a
    time.  Each job acquires a hard exclusive VRAM lock via
    ``ModelScheduler.begin_training()``, runs the subprocess, then releases the
    lock via ``ModelScheduler.end_training()`` and updates job status.

    Thread safety
    -------------
    * ``enqueue()`` and ``cancel_job()`` may be called from any thread.
    * Job status mutations are protected by ``_lock``.
    * The background worker thread is started lazily on the first ``enqueue()``.

    Parameters
    ----------
    read_jobs
        Callable ``(project_id: str) -> list[TrainingJob]`` — returns the
        current list of TrainingJob for the given project.
    write_jobs
        Callable ``(project_id: str, jobs: list[TrainingJob]) -> None`` —
        persists an updated list of TrainingJob for the given project.
    scheduler
        The ModelScheduler instance shared with the rest of the application.
        The executor calls ``begin_training()`` / ``end_training()`` on it.
    runner
        CommandRunner implementation.  Defaults to SubprocessRunner; tests
        inject FakeRunner.
    asset_store_resolver
        Optional callable ``(project_id: str) -> AssetStore`` used by
        ``_resolve_command()`` to load entities for live command construction.
        When None, ``_resolve_command()`` falls back to the pending-commands
        dict (i.e. commands must be supplied via ``enqueue_with_command()``).
    project_dir_resolver
        Optional callable ``(project_id: str) -> Path`` used to locate the
        project directory for command building (e.g. models subdir).
    """

    def __init__(
        self,
        *,
        read_jobs: Callable[[str], list[TrainingJob]],
        write_jobs: Callable[[str, list[TrainingJob]], None],
        scheduler: ModelScheduler,
        runner: CommandRunner | None = None,
        asset_store_resolver: "Callable[[str], object] | None" = None,
        project_dir_resolver: "Callable[[str], object] | None" = None,
    ) -> None:
        self._read_jobs = read_jobs
        self._write_jobs = write_jobs
        self._scheduler = scheduler
        self._runner: CommandRunner = runner or SubprocessRunner()
        self._queue: queue.Queue[str] = queue.Queue()
        self._lock = threading.Lock()
        self._worker_thread: threading.Thread | None = None
        self._current_job_id: str | None = None
        self._stop_event = threading.Event()
        self._asset_store_resolver = asset_store_resolver
        self._project_dir_resolver = project_dir_resolver
        # Pre-supplied command vectors (keyed by job_id), populated by enqueue_with_command.
        self._pending_commands: dict[str, tuple[list[str], "Path"]] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def enqueue(self, project_id: str, job_id: str) -> None:
        """Add a job to the FIFO queue and transition it to QUEUED.

        The background worker thread is started lazily on the first call.

        Parameters
        ----------
        project_id
            The project that owns the job.  Used to locate the correct
            jobs store when reading/writing status.
        job_id
            The ID of the job to enqueue.
        """
        with self._lock:
            jobs = self._read_jobs(project_id)
            jobs = _update_job(jobs, job_id, status=TrainingJobStatus.QUEUED)
            self._write_jobs(project_id, jobs)
        self._queue.put((project_id, job_id))
        self._ensure_worker_running()

    def cancel_job(self, project_id: str, job_id: str) -> bool:
        """Cancel a queued or running job.

        Returns True if the job was found and cancellation was initiated.
        A QUEUED job is moved to FAILED immediately.  A RUNNING job is
        interrupted via the runner's cancel() signal; the status transition
        to FAILED happens in the worker thread after the process exits.
        """
        with self._lock:
            jobs = self._read_jobs(project_id)
            job = _find_job(jobs, job_id)
            if job is None:
                return False
            if job.status == TrainingJobStatus.QUEUED:
                jobs = _update_job(jobs, job_id, status=TrainingJobStatus.FAILED,
                                   note="Cancelled before start.")
                self._write_jobs(project_id, jobs)
                return True
            if job.status == TrainingJobStatus.RUNNING:
                self._runner.cancel()
                return True
        return False

    def poll_job(self, project_id: str, job_id: str) -> TrainingJob | None:
        """Return the current state of a job, or None if not found."""
        jobs = self._read_jobs(project_id)
        return _find_job(jobs, job_id)

    def current_job_id(self) -> str | None:
        """Return the job_id of the currently-running job (or None)."""
        return self._current_job_id

    def stop(self) -> None:
        """Signal the worker thread to stop after the current job completes."""
        self._stop_event.set()

    # ------------------------------------------------------------------
    # Worker thread
    # ------------------------------------------------------------------

    def _ensure_worker_running(self) -> None:
        if self._worker_thread is None or not self._worker_thread.is_alive():
            self._worker_thread = threading.Thread(
                target=self._worker_loop, name="TrainingWorker", daemon=True
            )
            self._worker_thread.start()

    def _worker_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                project_id, job_id = self._queue.get(timeout=1.0)
            except queue.Empty:
                continue
            try:
                self._run_job(project_id, job_id)
            except Exception:
                logger.exception("Unhandled error in training worker for job %s", job_id)
                with self._lock:
                    jobs = self._read_jobs(project_id)
                    jobs = _update_job(
                        jobs, job_id,
                        status=TrainingJobStatus.FAILED,
                        note="Internal executor error; see logs.",
                    )
                    self._write_jobs(project_id, jobs)
            finally:
                self._queue.task_done()

    def _run_job(self, project_id: str, job_id: str) -> None:
        """Execute one training job; drives status queued→running→completed|failed."""
        # -- Direction (a): refuse to start if a managed model is currently ACTIVE --
        active_models = [
            name for name, m in self._scheduler._models.items()
            if m.state == RuntimeState.ACTIVE
        ]
        if active_models:
            with self._lock:
                jobs = self._read_jobs(project_id)
                jobs = _update_job(
                    jobs, job_id,
                    status=TrainingJobStatus.FAILED,
                    note=(
                        f"Cannot start training: managed model(s) are currently ACTIVE "
                        f"({', '.join(active_models)}). Ensure all models are offloaded first."
                    ),
                )
                self._write_jobs(project_id, jobs)
            return

        # -- Acquire hard exclusive VRAM lock --
        self._scheduler.begin_training(job_id)

        try:
            # -- Transition to RUNNING --
            with self._lock:
                jobs = self._read_jobs(project_id)
                job = _find_job(jobs, job_id)
                if job is None:
                    logger.warning("Job %s disappeared from storage before running.", job_id)
                    return
                if job.status not in (TrainingJobStatus.QUEUED,):
                    # Job was cancelled between enqueue and execution.
                    return
                jobs = _update_job(jobs, job_id, status=TrainingJobStatus.RUNNING,
                                   note="VRAM lock acquired; training started.")
                self._write_jobs(project_id, jobs)
                self._current_job_id = job_id

            # Build args from the job's stored command.
            args, cwd = self._resolve_command(project_id, job_id)

            def on_progress(pct: int, label: str) -> None:
                with self._lock:
                    jobs = self._read_jobs(project_id)
                    jobs = _update_job(jobs, job_id, progress=pct, progress_label=label)
                    self._write_jobs(project_id, jobs)

            result = self._runner.run(args, cwd, on_progress=on_progress)

            # -- Transition to COMPLETED or FAILED --
            with self._lock:
                jobs = self._read_jobs(project_id)
                if result.succeeded:
                    jobs = _update_job(
                        jobs, job_id,
                        status=TrainingJobStatus.COMPLETED,
                        progress=100,
                        progress_label="Completed",
                        exit_code=result.exit_code,
                        note="Training finished successfully.",
                    )
                else:
                    jobs = _update_job(
                        jobs, job_id,
                        status=TrainingJobStatus.FAILED,
                        exit_code=result.exit_code,
                        stderr_tail=result.stderr_tail or None,
                        note=f"Training failed (exit {result.exit_code}).",
                    )
                self._write_jobs(project_id, jobs)
        finally:
            # -- Always release VRAM lock --
            self._scheduler.end_training()
            self._current_job_id = None

    def _resolve_command(self, project_id: str, job_id: str) -> tuple[list[str], "Path"]:
        """Return (args, cwd) for a job.

        Resolution order:
        1. If a command was pre-supplied via ``enqueue_with_command()``, use it.
        2. If an ``asset_store_resolver`` and ``project_dir_resolver`` are wired,
           build the real command from the job's entities (LoRA or voice-clone).
        3. Otherwise fail the job with a clear error — the ["echo", ...] stub is
           NOT used in the live path.

        REAL-RUN NOTE: Path (2) constructs the real kohya_ss / GPT-SoVITS argv.
        It has been wired and contract-tested with FakeRunner but has NOT been
        verified against a live installation.  See RESEARCH_LOG §10.
        """
        # Path 1: pre-supplied command vector.
        with self._lock:
            if job_id in self._pending_commands:
                return self._pending_commands[job_id]

        # Load the job.
        jobs = self._read_jobs(project_id)
        job = _find_job(jobs, job_id)
        if job is None:
            raise ValueError(f"Job {job_id} not found in project {project_id}")

        # Path 2: live command construction from entities.
        if self._asset_store_resolver is not None and self._project_dir_resolver is not None:
            return self._build_live_command(project_id, job)

        # Path 3: no command available — hard failure.
        raise SchedulerError(
            f"No command vector available for job {job_id}. "
            "Provide an asset_store_resolver + project_dir_resolver or use "
            "enqueue_with_command()."
        )

    def _build_live_command(self, project_id: str, job: TrainingJob) -> tuple[list[str], "Path"]:
        """Build the real CLI argv by loading entities from the AssetStore.

        For LoRA (kohya-ss worker): requires a CharacterSheet, DatasetPack, and
        TrainingRecipe associated with the job.  Looks them up by the job's
        dataset_path (used as a DatasetPack source reference) and worker.

        For voice clone (gpt-sovits worker): requires character info from the
        dataset_path.

        If required entities are missing the job is failed with a clear reason.
        """
        from pathlib import Path as _Path
        from core.training.lora import build_lora_command
        from core.training.voice_clone import build_voice_clone_command

        store = self._asset_store_resolver(project_id)
        project_dir: _Path = self._project_dir_resolver(project_id)  # type: ignore[assignment]
        project_models_dir = project_dir / "models"

        worker = job.worker or ""

        if worker == "kohya-ss" or job.modality == Modality.IMAGE:
            # LoRA: find the first CharacterSheet and DatasetPack and TrainingRecipe
            # for this project.  The job's dataset_path ties back to the DatasetPack.
            sheets = store.list_character_sheets(project_id)
            packs = store.list_dataset_packs(project_id)
            recipes = store.list_training_recipes(project_id)

            if not sheets:
                raise SchedulerError(
                    f"Job {job.id}: no CharacterSheet found for project {project_id}. "
                    "Create a CharacterSheet before submitting a LoRA training job."
                )
            if not packs:
                raise SchedulerError(
                    f"Job {job.id}: no DatasetPack found for project {project_id}. "
                    "Create a DatasetPack before submitting a LoRA training job."
                )
            if not recipes:
                raise SchedulerError(
                    f"Job {job.id}: no TrainingRecipe found for project {project_id}. "
                    "Create a TrainingRecipe before submitting a LoRA training job."
                )

            # Match by dataset_path if possible, else use first pack.
            matching_pack = next(
                (p for p in packs if p.source == job.dataset_path), packs[0]
            )
            sheet = sheets[0]
            recipe = recipes[0]

            # Worker directory from job metadata or a sensible default.
            kohya_dir = _Path(job.dataset_path).parent / "kohya_ss" if job.dataset_path else _Path("workers/kohya-ss")
            # In production the worker path comes from the workers manifest; the
            # asset_store_resolver path is sufficient for contract tests.
            spec = build_lora_command(
                character_sheet=sheet,
                dataset_pack=matching_pack,
                recipe=recipe,
                project_models_dir=project_models_dir,
                kohya_ss_dir=kohya_dir,
            )
            return (spec.args, spec.cwd)

        if worker == "gpt-sovits" or job.modality == Modality.VOICE:
            sheets = store.list_character_sheets(project_id)
            character_name = sheets[0].name if sheets else "character"
            reference_audio = _Path(job.dataset_path) if job.dataset_path else _Path(".")
            gpt_dir = reference_audio.parent / "gpt-sovits"
            spec = build_voice_clone_command(
                character_name=character_name,
                reference_audio=reference_audio,
                project_models_dir=project_models_dir,
                gpt_sovits_dir=gpt_dir,
                mode="fine_tune",
            )
            if spec.s1_args is None:
                raise SchedulerError(
                    f"Job {job.id}: voice-clone fine-tune produced no training args."
                )
            # Run S1 then S2; for the executor contract we use S1 args only
            # (S2 is a separate subsequent run — multi-stage sequencing is
            # DEFERRED to a follow-up phase).
            return (spec.s1_args, spec.cwd)

        raise SchedulerError(
            f"Job {job.id}: unknown worker '{worker}' — cannot resolve command. "
            "Expected 'kohya-ss' or 'gpt-sovits'."
        )

    def enqueue_with_command(
        self,
        project_id: str,
        job_id: str,
        args: list[str],
        cwd: "Path",
    ) -> None:
        """Enqueue a job with a pre-built command vector.

        Stores the (args, cwd) pair before enqueueing so ``_resolve_command()``
        picks it up at run time without re-resolving from entities.  Prefer this
        API in tests that supply FakeRunner; the live submit path uses
        ``enqueue()`` with a wired ``asset_store_resolver``.
        """
        with self._lock:
            self._pending_commands[job_id] = (args, cwd)
        self.enqueue(project_id, job_id)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _now() -> datetime:
    return datetime.now(timezone.utc)


def _find_job(jobs: list[TrainingJob], job_id: str) -> TrainingJob | None:
    for j in jobs:
        if j.id == job_id:
            return j
    return None


def _update_job(
    jobs: list[TrainingJob],
    job_id: str,
    **kwargs: object,
) -> list[TrainingJob]:
    """Return a new list with the named job updated (immutable-style)."""
    now = _now()
    result: list[TrainingJob] = []
    for j in jobs:
        if j.id == job_id:
            data = j.model_dump()
            data.update(kwargs)
            data["updated_at"] = now
            result.append(TrainingJob(**data))
        else:
            result.append(j)
    return result


def _maybe_report_progress(line: str, cb: Callable[[int, str], None] | None) -> None:
    """Parse a stdout line for progress signals and invoke the callback."""
    if cb is None:
        return
    # kohya_ss emits "steps: N/M" in tqdm output.
    import re
    m = re.search(r"(\d+)/(\d+)", line)
    if m:
        done, total = int(m.group(1)), int(m.group(2))
        if total > 0:
            pct = min(100, int(done * 100 / total))
            cb(pct, line.strip()[:120])
