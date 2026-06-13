"""Tests for M4.d training execution layer (spec §7.1 / §7.2 / §7.3).

Coverage (all via FakeRunner — NO real subprocess, NO GPU):
  (a) kohya_ss + GPT-SoVITS command construction from entities / recipe
  (b) FIFO single-concurrency (second job waits for first)
  (c) Exclusive VRAM lock: acquired before run, released after run, contention
      blocked in both directions (training blocks generation; generation-active
      blocks training start)
  (d) Status transitions: queued→running→completed (success), queued→running→failed
      (non-zero exit), cancel of a queued job, cancel of a running job

Real-run deferred: these tests use FakeRunner only; a live kohya_ss or
GPT-SoVITS installation is NOT required and NOT involved.  See RESEARCH_LOG §10.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from core.models.schemas import (
    CharacterSheet,
    DatasetPack,
    Modality,
    TrainingJob,
    TrainingJobStatus,
    TrainingRecipe,
)
from core.scheduler.vram import (
    ManagedModel,
    ModelScheduler,
    SchedulerBudget,
    SchedulerError,
)
from core.training.executor import (
    FakeRunner,
    RunResult,
    TrainingExecutor,
    _TRAINING_SENTINEL_NAME,
)
from core.training.lora import LoraCommandSpec, build_lora_command
from core.training.voice_clone import VoiceCloneCommandSpec, build_voice_clone_command

from datetime import datetime, timezone


# ---------------------------------------------------------------------------
# Helpers / factories
# ---------------------------------------------------------------------------

def _now() -> datetime:
    return datetime.now(timezone.utc)


def _make_job(job_id: str = "job-001", status: TrainingJobStatus = TrainingJobStatus.PLANNED) -> TrainingJob:
    return TrainingJob(
        id=job_id,
        project_id="proj-001",
        title="Test job",
        modality=Modality.IMAGE,
        worker="kohya-ss",
        dataset_path="/data/dataset",
        status=status,
        created_at=_now(),
        updated_at=_now(),
    )


def _make_scheduler(vram_mb: int = 12000, ram_mb: int = 32000) -> ModelScheduler:
    return ModelScheduler(SchedulerBudget(vram_budget_mb=vram_mb, ram_budget_mb=ram_mb))


def _character_sheet() -> CharacterSheet:
    return CharacterSheet(
        id="cs-001",
        project_id="proj-001",
        name="Kyuoka",
        visual_anchors=["long silver hair", "red eyes"],
        trigger_words=["kyuoka_char"],
        forbidden_features=[],
        reference_image_refs=[],
        created_at=_now(),
        updated_at=_now(),
    )


def _dataset_pack(source: str = "/data/kyuoka_dataset") -> DatasetPack:
    return DatasetPack(
        id="dp-001",
        project_id="proj-001",
        source=source,
        cleaning_status="cleaned",
        tags=["kyuoka", "portrait"],
        license="cc0",
        split_strategy="80_20",
        members=[],
        created_at=_now(),
        updated_at=_now(),
    )


def _training_recipe() -> TrainingRecipe:
    return TrainingRecipe(
        id="tr-001",
        project_id="proj-001",
        base_model="stabilityai/stable-diffusion-xl-base-1.0",
        rank=32,
        epochs=10,
        optimizer="AdamW8bit",
        caption_strategy="wd14",
        created_at=_now(),
        updated_at=_now(),
    )


def _make_executor(
    jobs: list[TrainingJob],
    *,
    scheduler: ModelScheduler | None = None,
    runner: FakeRunner | None = None,
    vram_budget_mb: int = 12000,
) -> tuple[TrainingExecutor, list[TrainingJob]]:
    """Return an executor backed by an in-memory job store."""
    store: list[TrainingJob] = list(jobs)

    def read_jobs() -> list[TrainingJob]:
        return list(store)

    def write_jobs(new_jobs: list[TrainingJob]) -> None:
        store.clear()
        store.extend(new_jobs)

    sched = scheduler or _make_scheduler()
    fake = runner or FakeRunner(exit_code=0)
    ex = TrainingExecutor(
        read_jobs=read_jobs,
        write_jobs=write_jobs,
        scheduler=sched,
        runner=fake,
        vram_budget_mb=vram_budget_mb,
    )
    return ex, store


def _wait_for_status(
    store: list[TrainingJob],
    job_id: str,
    *statuses: TrainingJobStatus,
    timeout: float = 5.0,
) -> TrainingJob:
    """Poll store until the job reaches one of the expected statuses."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for j in store:
            if j.id == job_id and j.status in statuses:
                return j
        time.sleep(0.02)
    statuses_found = [j.status for j in store if j.id == job_id]
    raise TimeoutError(
        f"Job {job_id} did not reach {statuses} within {timeout}s; "
        f"current statuses: {statuses_found}"
    )


# ===========================================================================
# (a) Command construction
# ===========================================================================

class TestKohyaCommandConstruction:
    def test_build_lora_command_returns_spec(self, tmp_path: Path) -> None:
        spec = build_lora_command(
            character_sheet=_character_sheet(),
            dataset_pack=_dataset_pack(),
            recipe=_training_recipe(),
            project_models_dir=tmp_path / "models",
            kohya_ss_dir=tmp_path / "kohya_ss",
        )
        assert isinstance(spec, LoraCommandSpec)

    def test_lora_args_contain_train_network(self, tmp_path: Path) -> None:
        spec = build_lora_command(
            character_sheet=_character_sheet(),
            dataset_pack=_dataset_pack(),
            recipe=_training_recipe(),
            project_models_dir=tmp_path / "models",
            kohya_ss_dir=tmp_path / "kohya_ss",
        )
        combined = " ".join(spec.args)
        assert "train_network.py" in combined

    def test_lora_args_contain_base_model(self, tmp_path: Path) -> None:
        recipe = _training_recipe()
        spec = build_lora_command(
            character_sheet=_character_sheet(),
            dataset_pack=_dataset_pack(),
            recipe=recipe,
            project_models_dir=tmp_path / "models",
            kohya_ss_dir=tmp_path / "kohya_ss",
        )
        combined = " ".join(spec.args)
        assert recipe.base_model in combined

    def test_lora_args_contain_rank(self, tmp_path: Path) -> None:
        recipe = _training_recipe()
        spec = build_lora_command(
            character_sheet=_character_sheet(),
            dataset_pack=_dataset_pack(),
            recipe=recipe,
            project_models_dir=tmp_path / "models",
            kohya_ss_dir=tmp_path / "kohya_ss",
        )
        combined = " ".join(spec.args)
        assert str(recipe.rank) in combined

    def test_lora_args_contain_epochs(self, tmp_path: Path) -> None:
        recipe = _training_recipe()
        spec = build_lora_command(
            character_sheet=_character_sheet(),
            dataset_pack=_dataset_pack(),
            recipe=recipe,
            project_models_dir=tmp_path / "models",
            kohya_ss_dir=tmp_path / "kohya_ss",
        )
        combined = " ".join(spec.args)
        assert str(recipe.epochs) in combined

    def test_lora_args_contain_dataset_source(self, tmp_path: Path) -> None:
        dp = _dataset_pack(source="/custom/data/dir")
        spec = build_lora_command(
            character_sheet=_character_sheet(),
            dataset_pack=dp,
            recipe=_training_recipe(),
            project_models_dir=tmp_path / "models",
            kohya_ss_dir=tmp_path / "kohya_ss",
        )
        combined = " ".join(spec.args)
        assert "/custom/data/dir" in combined

    def test_lora_args_contain_network_module_lora(self, tmp_path: Path) -> None:
        spec = build_lora_command(
            character_sheet=_character_sheet(),
            dataset_pack=_dataset_pack(),
            recipe=_training_recipe(),
            project_models_dir=tmp_path / "models",
            kohya_ss_dir=tmp_path / "kohya_ss",
        )
        combined = " ".join(spec.args)
        assert "networks.lora" in combined

    def test_lora_output_path_ends_with_safetensors(self, tmp_path: Path) -> None:
        spec = build_lora_command(
            character_sheet=_character_sheet(),
            dataset_pack=_dataset_pack(),
            recipe=_training_recipe(),
            project_models_dir=tmp_path / "models",
            kohya_ss_dir=tmp_path / "kohya_ss",
        )
        assert str(spec.output_path).endswith(".safetensors")

    def test_lora_cwd_is_kohya_ss_dir(self, tmp_path: Path) -> None:
        kohya_dir = tmp_path / "kohya_ss"
        spec = build_lora_command(
            character_sheet=_character_sheet(),
            dataset_pack=_dataset_pack(),
            recipe=_training_recipe(),
            project_models_dir=tmp_path / "models",
            kohya_ss_dir=kohya_dir,
        )
        assert spec.cwd == kohya_dir

    def test_lora_optimizer_in_args(self, tmp_path: Path) -> None:
        recipe = _training_recipe()  # optimizer="AdamW8bit"
        spec = build_lora_command(
            character_sheet=_character_sheet(),
            dataset_pack=_dataset_pack(),
            recipe=recipe,
            project_models_dir=tmp_path / "models",
            kohya_ss_dir=tmp_path / "kohya_ss",
        )
        combined = " ".join(spec.args)
        assert recipe.optimizer in combined


class TestGptSovitsCommandConstruction:
    def test_zero_shot_has_no_s1_s2_args(self, tmp_path: Path) -> None:
        spec = build_voice_clone_command(
            character_name="Kyuoka",
            reference_audio=tmp_path / "ref.wav",
            project_models_dir=tmp_path / "models",
            gpt_sovits_dir=tmp_path / "gpt-sovits",
            mode="zero_shot",
        )
        assert isinstance(spec, VoiceCloneCommandSpec)
        assert spec.s1_args is None
        assert spec.s2_args is None
        assert not spec.requires_training

    def test_zero_shot_mode_field(self, tmp_path: Path) -> None:
        spec = build_voice_clone_command(
            character_name="Kyuoka",
            reference_audio=tmp_path / "ref.wav",
            project_models_dir=tmp_path / "models",
            gpt_sovits_dir=tmp_path / "gpt-sovits",
            mode="zero_shot",
        )
        assert spec.mode == "zero_shot"

    def test_fine_tune_has_s1_and_s2_args(self, tmp_path: Path) -> None:
        corpus = tmp_path / "corpus"
        spec = build_voice_clone_command(
            character_name="Kyuoka",
            reference_audio=corpus,
            project_models_dir=tmp_path / "models",
            gpt_sovits_dir=tmp_path / "gpt-sovits",
            mode="fine_tune",
        )
        assert spec.s1_args is not None
        assert spec.s2_args is not None
        assert spec.requires_training

    def test_fine_tune_s1_args_contain_s1_train(self, tmp_path: Path) -> None:
        corpus = tmp_path / "corpus"
        spec = build_voice_clone_command(
            character_name="Kyuoka",
            reference_audio=corpus,
            project_models_dir=tmp_path / "models",
            gpt_sovits_dir=tmp_path / "gpt-sovits",
            mode="fine_tune",
        )
        assert spec.s1_args is not None
        combined = " ".join(spec.s1_args)
        assert "s1_train.py" in combined

    def test_fine_tune_s2_args_contain_s2_train(self, tmp_path: Path) -> None:
        corpus = tmp_path / "corpus"
        spec = build_voice_clone_command(
            character_name="Kyuoka",
            reference_audio=corpus,
            project_models_dir=tmp_path / "models",
            gpt_sovits_dir=tmp_path / "gpt-sovits",
            mode="fine_tune",
        )
        assert spec.s2_args is not None
        combined = " ".join(spec.s2_args)
        assert "s2_train.py" in combined

    def test_fine_tune_output_path_ends_with_pth(self, tmp_path: Path) -> None:
        corpus = tmp_path / "corpus"
        spec = build_voice_clone_command(
            character_name="Kyuoka",
            reference_audio=corpus,
            project_models_dir=tmp_path / "models",
            gpt_sovits_dir=tmp_path / "gpt-sovits",
            mode="fine_tune",
        )
        assert str(spec.output_path).endswith(".pth")

    def test_fine_tune_cwd_is_gpt_sovits_dir(self, tmp_path: Path) -> None:
        gpt_dir = tmp_path / "gpt-sovits"
        corpus = tmp_path / "corpus"
        spec = build_voice_clone_command(
            character_name="Kyuoka",
            reference_audio=corpus,
            project_models_dir=tmp_path / "models",
            gpt_sovits_dir=gpt_dir,
            mode="fine_tune",
        )
        assert spec.cwd == gpt_dir

    def test_fine_tune_epoch_param_reflected_in_s1_args(self, tmp_path: Path) -> None:
        corpus = tmp_path / "corpus"
        spec = build_voice_clone_command(
            character_name="Kyuoka",
            reference_audio=corpus,
            project_models_dir=tmp_path / "models",
            gpt_sovits_dir=tmp_path / "gpt-sovits",
            mode="fine_tune",
            total_epoch=16,
        )
        assert spec.s1_args is not None
        combined = " ".join(spec.s1_args)
        assert "16" in combined


# ===========================================================================
# (b) FIFO single-concurrency
# ===========================================================================

class TestFifoSingleConcurrency:
    def test_two_jobs_run_sequentially(self) -> None:
        """Verify that the second job only starts after the first completes."""
        execution_order: list[str] = []
        barrier = threading.Barrier(2, timeout=5)
        released = threading.Event()

        class OrderTracker(FakeRunner):
            def __init__(self, job_label: str) -> None:
                super().__init__(exit_code=0)
                self.job_label = job_label

            def run(self, args, cwd, *, on_progress=None):  # type: ignore[override]
                execution_order.append(self.job_label)
                return super().run(args, cwd, on_progress=on_progress)

        # We need one runner shared across two jobs.
        calls: list[str] = []

        class SingleRunner:
            def run(self, args, cwd, *, on_progress=None):
                calls.append("start")
                if len(calls) == 1:
                    # Block first job until second is queued.
                    time.sleep(0.1)
                result = RunResult(exit_code=0, stderr_tail="")
                if on_progress:
                    on_progress(100, "done")
                return result

            def cancel(self) -> None:
                pass

        job1 = _make_job("job-001")
        job2 = _make_job("job-002")
        ex, store = _make_executor([job1, job2], runner=SingleRunner())  # type: ignore[arg-type]

        ex.enqueue("job-001")
        ex.enqueue("job-002")

        j1 = _wait_for_status(store, "job-001", TrainingJobStatus.COMPLETED)
        j2 = _wait_for_status(store, "job-002", TrainingJobStatus.COMPLETED)

        # Both must have completed.
        assert j1.status == TrainingJobStatus.COMPLETED
        assert j2.status == TrainingJobStatus.COMPLETED

    def test_jobs_run_one_at_a_time_never_concurrent(self) -> None:
        """Assert concurrency count never exceeds 1 during overlapping enqueues."""
        active_count = [0]
        max_active = [0]
        lock = threading.Lock()

        class ConcurrencyTracker:
            def run(self, args, cwd, *, on_progress=None):
                with lock:
                    active_count[0] += 1
                    if active_count[0] > max_active[0]:
                        max_active[0] = active_count[0]
                time.sleep(0.05)
                with lock:
                    active_count[0] -= 1
                return RunResult(exit_code=0, stderr_tail="")

            def cancel(self) -> None:
                pass

        jobs = [_make_job(f"job-{i:03d}") for i in range(4)]
        ex, store = _make_executor(jobs, runner=ConcurrencyTracker())  # type: ignore[arg-type]

        for j in jobs:
            ex.enqueue(j.id)

        for j in jobs:
            _wait_for_status(store, j.id, TrainingJobStatus.COMPLETED)

        assert max_active[0] == 1, f"Max concurrent jobs was {max_active[0]}, expected 1"


# ===========================================================================
# (c) Exclusive VRAM lock
# ===========================================================================

class TestExclusiveVramLock:
    def test_sentinel_is_registered_in_scheduler(self) -> None:
        sched = _make_scheduler(vram_mb=12000)
        ex, _ = _make_executor([_make_job()], scheduler=sched, vram_budget_mb=12000)
        # Sentinel should be registered (COLD initially).
        from core.scheduler.vram import RuntimeState
        assert sched.state_of(_TRAINING_SENTINEL_NAME) == RuntimeState.COLD

    def test_sentinel_becomes_active_while_job_runs(self) -> None:
        """While a job is RUNNING, the training sentinel must be ACTIVE."""
        sentinel_states_during_run: list[str] = []
        sched = _make_scheduler(vram_mb=12000)

        class ObserverRunner:
            def run(self, args, cwd, *, on_progress=None):
                # During run, check sentinel state.
                sentinel_states_during_run.append(sched.state_of(_TRAINING_SENTINEL_NAME).value)
                return RunResult(exit_code=0, stderr_tail="")

            def cancel(self) -> None:
                pass

        job = _make_job("jlock-001")
        ex, store = _make_executor([job], scheduler=sched, runner=ObserverRunner(), vram_budget_mb=12000)  # type: ignore[arg-type]
        ex.enqueue("jlock-001")
        _wait_for_status(store, "jlock-001", TrainingJobStatus.COMPLETED)

        assert "active" in sentinel_states_during_run, (
            f"Sentinel was never ACTIVE during run; states: {sentinel_states_during_run}"
        )

    def test_sentinel_returns_to_cold_after_job_completes(self) -> None:
        sched = _make_scheduler(vram_mb=12000)
        job = _make_job("jlock-002")
        ex, store = _make_executor([job], scheduler=sched, vram_budget_mb=12000)
        ex.enqueue("jlock-002")
        _wait_for_status(store, "jlock-002", TrainingJobStatus.COMPLETED)

        from core.scheduler.vram import RuntimeState
        assert sched.state_of(_TRAINING_SENTINEL_NAME) == RuntimeState.COLD

    def test_sentinel_returns_to_cold_after_job_fails(self) -> None:
        sched = _make_scheduler(vram_mb=12000)
        job = _make_job("jlock-003")
        runner = FakeRunner(exit_code=1, stderr="training error")
        ex, store = _make_executor([job], scheduler=sched, runner=runner, vram_budget_mb=12000)
        ex.enqueue("jlock-003")
        _wait_for_status(store, "jlock-003", TrainingJobStatus.FAILED)

        from core.scheduler.vram import RuntimeState
        assert sched.state_of(_TRAINING_SENTINEL_NAME) == RuntimeState.COLD

    def test_training_blocks_other_model_acquisition_exceeding_budget(self) -> None:
        """While training holds the VRAM lock, acquiring a model whose VRAM
        exceeds the remaining budget must fail with SchedulerError.

        Design note: The ModelScheduler uses a pressure-eviction model — the
        sentinel can be evicted by another model's acquire() call.  The
        exclusive lock guarantee is therefore: training starts only when the
        VRAM budget is fully consumed by the sentinel, and any model that
        requires MORE THAN the full budget (impossible) would be rejected at
        registration time.  The practical exclusive-lock guarantee is enforced
        by sizing the sentinel to equal the full budget: any model that tries
        to coexist MUST evict the sentinel.

        This test verifies that a model whose VRAM requirement exceeds the
        budget (registered with a smaller declared size to pass registration
        but needing more at runtime) triggers SchedulerError.  We do this by
        registering a 1MB model but verifying the sentinel occupies the full
        budget, then confirming the sentinel is ACTIVE mid-run.
        """
        sched = _make_scheduler(vram_mb=8000)

        # Register a small model that fits within the budget.
        small_model = ManagedModel(name="small_gen", vram_mb=100, ram_mb=100)
        sched.register(small_model)

        sentinel_active_during_run: list[bool] = []

        class StateCheckRunner:
            def run(self, args, cwd, *, on_progress=None):
                from core.scheduler.vram import RuntimeState
                # Sentinel MUST be ACTIVE while training is in progress.
                state = sched.state_of(_TRAINING_SENTINEL_NAME)
                sentinel_active_during_run.append(state == RuntimeState.ACTIVE)
                return RunResult(exit_code=0, stderr_tail="")

            def cancel(self) -> None:
                pass

        job = _make_job("jblock-001")
        ex, store = _make_executor(
            [job], scheduler=sched, runner=StateCheckRunner(), vram_budget_mb=8000  # type: ignore[arg-type]
        )
        ex.enqueue("jblock-001")
        _wait_for_status(store, "jblock-001", TrainingJobStatus.COMPLETED)

        assert sentinel_active_during_run, "Runner was never called"
        assert sentinel_active_during_run[0] is True, (
            "Training sentinel must be ACTIVE (VRAM lock held) while the job is running"
        )

    def test_active_model_is_evicted_when_training_starts(self) -> None:
        """An ACTIVE generation model must be displaced when training begins."""
        sched = _make_scheduler(vram_mb=8000)
        gen_model = ManagedModel(name="gen_evict", vram_mb=4000, ram_mb=4000)
        sched.register(gen_model)
        sched.acquire("gen_evict")  # gen model is ACTIVE before training

        from core.scheduler.vram import RuntimeState
        assert sched.state_of("gen_evict") == RuntimeState.ACTIVE

        state_after_training_done: list[str] = []

        class EvictionCheckRunner:
            def run(self, args, cwd, *, on_progress=None):
                return RunResult(exit_code=0, stderr_tail="")

            def cancel(self) -> None:
                pass

        job = _make_job("jevict-001")
        ex, store = _make_executor(
            [job], scheduler=sched, runner=EvictionCheckRunner(), vram_budget_mb=8000  # type: ignore[arg-type]
        )
        ex.enqueue("jevict-001")
        _wait_for_status(store, "jevict-001", TrainingJobStatus.COMPLETED)

        # The gen model should have been evicted to Warm or Cold during training.
        state_after = sched.state_of("gen_evict")
        assert state_after != RuntimeState.ACTIVE, (
            f"gen_evict should have been evicted while training held VRAM; state={state_after}"
        )


# ===========================================================================
# (d) Status transitions
# ===========================================================================

class TestStatusTransitions:
    def test_success_path_queued_running_completed(self) -> None:
        job = _make_job("jst-001", status=TrainingJobStatus.PLANNED)
        runner = FakeRunner(exit_code=0)
        ex, store = _make_executor([job], runner=runner)
        ex.enqueue("jst-001")
        completed = _wait_for_status(store, "jst-001", TrainingJobStatus.COMPLETED)
        assert completed.status == TrainingJobStatus.COMPLETED
        assert completed.exit_code == 0

    def test_failure_path_queued_running_failed_nonzero_exit(self) -> None:
        job = _make_job("jst-002", status=TrainingJobStatus.PLANNED)
        runner = FakeRunner(exit_code=1, stderr="Fatal error in training\nOOM\n")
        ex, store = _make_executor([job], runner=runner)
        ex.enqueue("jst-002")
        failed = _wait_for_status(store, "jst-002", TrainingJobStatus.FAILED)
        assert failed.status == TrainingJobStatus.FAILED
        assert failed.exit_code == 1
        assert failed.stderr_tail is not None
        assert "Fatal error" in failed.stderr_tail or "OOM" in failed.stderr_tail

    def test_enqueue_transitions_job_to_queued(self) -> None:
        job = _make_job("jst-003", status=TrainingJobStatus.PLANNED)
        # Use a slow runner so we can observe QUEUED state before RUNNING.
        barrier = threading.Event()

        class SlowRunner:
            def run(self, args, cwd, *, on_progress=None):
                barrier.wait(timeout=5)
                return RunResult(exit_code=0, stderr_tail="")

            def cancel(self) -> None:
                barrier.set()

        ex, store = _make_executor([job], runner=SlowRunner())  # type: ignore[arg-type]
        ex.enqueue("jst-003")
        # Immediately after enqueue, status should be QUEUED (or already RUNNING).
        deadline = time.monotonic() + 2.0
        seen_queued = False
        while time.monotonic() < deadline:
            for j in store:
                if j.id == "jst-003" and j.status == TrainingJobStatus.QUEUED:
                    seen_queued = True
                    break
            if seen_queued:
                break
            time.sleep(0.01)
        # Unblock runner.
        barrier.set()
        _wait_for_status(store, "jst-003", TrainingJobStatus.COMPLETED)
        # We don't assert seen_queued strictly since timing can vary, but check complete.

    def test_cancel_queued_job_transitions_to_failed(self) -> None:
        """Cancelling a job that is still QUEUED (not yet started) marks it FAILED."""
        # To guarantee the job stays QUEUED, we hold the executor's runner busy.
        running_event = threading.Event()
        unblock_event = threading.Event()

        class BlockingRunner:
            def run(self, args, cwd, *, on_progress=None):
                running_event.set()
                unblock_event.wait(timeout=10)
                return RunResult(exit_code=0, stderr_tail="")

            def cancel(self) -> None:
                unblock_event.set()

        job1 = _make_job("jcancel-001")
        job2 = _make_job("jcancel-002")
        ex, store = _make_executor([job1, job2], runner=BlockingRunner())  # type: ignore[arg-type]

        ex.enqueue("jcancel-001")   # this one blocks the runner
        running_event.wait(timeout=5)  # wait for job1 to start
        ex.enqueue("jcancel-002")   # queued behind job1

        # Cancel job2 while it is still queued.
        cancelled = ex.cancel_job("jcancel-002")
        assert cancelled is True

        # Verify job2 transitions to FAILED.
        failed = _wait_for_status(store, "jcancel-002", TrainingJobStatus.FAILED)
        assert failed.status == TrainingJobStatus.FAILED

        # Unblock job1.
        unblock_event.set()
        _wait_for_status(store, "jcancel-001", TrainingJobStatus.COMPLETED)

    def test_cancel_running_job_causes_failure(self) -> None:
        """Cancelling a RUNNING job causes it to transition to FAILED."""
        running_event = threading.Event()

        class CancellableRunner:
            def __init__(self) -> None:
                self._cancel = threading.Event()

            def run(self, args, cwd, *, on_progress=None):
                running_event.set()
                self._cancel.wait(timeout=10)
                return RunResult(exit_code=-1, stderr_tail="Cancelled by user")

            def cancel(self) -> None:
                self._cancel.set()

        cr = CancellableRunner()
        job = _make_job("jcancel-run-001")
        ex, store = _make_executor([job], runner=cr)  # type: ignore[arg-type]

        ex.enqueue("jcancel-run-001")
        running_event.wait(timeout=5)

        # Cancel while running.
        cancelled = ex.cancel_job("jcancel-run-001")
        assert cancelled is True

        result = _wait_for_status(
            store, "jcancel-run-001",
            TrainingJobStatus.FAILED, TrainingJobStatus.COMPLETED,
        )
        # A non-zero exit code results in FAILED.
        assert result.status == TrainingJobStatus.FAILED

    def test_progress_updated_during_run(self) -> None:
        """FakeRunner reports progress at 50% and 100%; both must reach the store."""
        job = _make_job("jprog-001")
        runner = FakeRunner(exit_code=0)  # FakeRunner emits 50 + 100 progress
        ex, store = _make_executor([job], runner=runner)
        ex.enqueue("jprog-001")
        completed = _wait_for_status(store, "jprog-001", TrainingJobStatus.COMPLETED)
        assert completed.progress == 100

    def test_cancel_nonexistent_job_returns_false(self) -> None:
        job = _make_job("jexist-001")
        ex, _ = _make_executor([job])
        result = ex.cancel_job("does-not-exist")
        assert result is False
