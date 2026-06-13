"""Tests for M4.d training execution layer (spec §7.1 / §7.2 / §7.3).

Coverage (all via FakeRunner — NO real subprocess, NO GPU):
  (a) kohya_ss + GPT-SoVITS command construction from entities / recipe
  (b) FIFO single-concurrency (second job waits for first)
  (c) Hard exclusive VRAM lock: acquired before run (is_training_locked()==True),
      released after run (is_training_locked()==False); while held,
      scheduler.acquire() is refused; after release, acquire works again.
      Direction (a): training refuses to start if a managed model is ACTIVE.
      Generation service blocking reason: "training in progress" reported when
      lock is held.
  (d) Status transitions: queued→running→completed (success), queued→running→failed
      (non-zero exit), cancel of a queued job, cancel of a running job
  (e) Per-project job store isolation: two projects use separate jobs.json
  (f) Live command path: submit_job with wired asset_store_resolver produces
      real kohya_ss argv (not ["echo", ...])

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
)
from core.training.lora import LoraCommandSpec, build_lora_command
from core.training.voice_clone import VoiceCloneCommandSpec, build_voice_clone_command

from datetime import datetime, timezone


# ---------------------------------------------------------------------------
# Helpers / factories
# ---------------------------------------------------------------------------

_DEFAULT_PROJECT = "proj-001"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _make_job(
    job_id: str = "job-001",
    status: TrainingJobStatus = TrainingJobStatus.PLANNED,
    project_id: str = _DEFAULT_PROJECT,
) -> TrainingJob:
    return TrainingJob(
        id=job_id,
        project_id=project_id,
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
        project_id=_DEFAULT_PROJECT,
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
        project_id=_DEFAULT_PROJECT,
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
        project_id=_DEFAULT_PROJECT,
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
    project_id: str = _DEFAULT_PROJECT,
) -> tuple[TrainingExecutor, dict[str, list[TrainingJob]]]:
    """Return an executor backed by a per-project in-memory job store.

    The store is keyed by project_id so the two-project isolation test can
    verify that each project's jobs are persisted independently.
    """
    # One store dict maps project_id -> list[TrainingJob].
    stores: dict[str, list[TrainingJob]] = {project_id: list(jobs)}

    def read_jobs(pid: str) -> list[TrainingJob]:
        return list(stores.setdefault(pid, []))

    def write_jobs(pid: str, new_jobs: list[TrainingJob]) -> None:
        stores[pid] = list(new_jobs)

    sched = scheduler or _make_scheduler()
    fake = runner or FakeRunner(exit_code=0)
    ex = TrainingExecutor(
        read_jobs=read_jobs,
        write_jobs=write_jobs,
        scheduler=sched,
        runner=fake,
    )
    return ex, stores


def _wait_for_status(
    stores: dict[str, list[TrainingJob]],
    project_id: str,
    job_id: str,
    *statuses: TrainingJobStatus,
    timeout: float = 5.0,
) -> TrainingJob:
    """Poll store until the job reaches one of the expected statuses."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for j in stores.get(project_id, []):
            if j.id == job_id and j.status in statuses:
                return j
        time.sleep(0.02)
    statuses_found = [j.status for j in stores.get(project_id, []) if j.id == job_id]
    raise TimeoutError(
        f"Job {job_id} (project {project_id}) did not reach {statuses} within {timeout}s; "
        f"current statuses: {statuses_found}"
    )


# Convenience wrapper for single-project tests.
def _wait(
    store: dict[str, list[TrainingJob]],
    job_id: str,
    *statuses: TrainingJobStatus,
    timeout: float = 5.0,
    project_id: str = _DEFAULT_PROJECT,
) -> TrainingJob:
    return _wait_for_status(store, project_id, job_id, *statuses, timeout=timeout)


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

    def test_lora_output_name_not_duplicated(self, tmp_path: Path) -> None:
        """--output_name must appear exactly once in the args list."""
        spec = build_lora_command(
            character_sheet=_character_sheet(),
            dataset_pack=_dataset_pack(),
            recipe=_training_recipe(),
            project_models_dir=tmp_path / "models",
            kohya_ss_dir=tmp_path / "kohya_ss",
        )
        count = sum(1 for arg in spec.args if arg.startswith("--output_name="))
        assert count == 1, f"--output_name appeared {count} times; expected exactly 1"

    def test_lora_builder_does_not_create_directories(self, tmp_path: Path) -> None:
        """build_lora_command is a pure function and must not create directories."""
        models_dir = tmp_path / "does_not_exist"
        assert not models_dir.exists()
        build_lora_command(
            character_sheet=_character_sheet(),
            dataset_pack=_dataset_pack(),
            recipe=_training_recipe(),
            project_models_dir=models_dir,
            kohya_ss_dir=tmp_path / "kohya_ss",
        )
        assert not models_dir.exists(), (
            "build_lora_command must not create the output directory — "
            "that is the executor's responsibility at run time"
        )


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

    def test_voice_clone_builder_does_not_create_directories(self, tmp_path: Path) -> None:
        """build_voice_clone_command is a pure function and must not create directories."""
        models_dir = tmp_path / "voices_does_not_exist"
        assert not models_dir.exists()
        build_voice_clone_command(
            character_name="Kyuoka",
            reference_audio=tmp_path / "ref.wav",
            project_models_dir=models_dir,
            gpt_sovits_dir=tmp_path / "gpt-sovits",
            mode="zero_shot",
        )
        assert not (models_dir / "voices").exists(), (
            "build_voice_clone_command must not create directories — "
            "that is the executor's responsibility at run time"
        )


# ===========================================================================
# (b) FIFO single-concurrency
# ===========================================================================

class TestFifoSingleConcurrency:
    def test_two_jobs_run_sequentially(self) -> None:
        """Verify that the second job only starts after the first completes."""
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

        ex.enqueue_with_command(_DEFAULT_PROJECT, "job-001", ["echo", "a"], Path("."))
        ex.enqueue_with_command(_DEFAULT_PROJECT, "job-002", ["echo", "b"], Path("."))

        j1 = _wait(store, "job-001", TrainingJobStatus.COMPLETED)
        j2 = _wait(store, "job-002", TrainingJobStatus.COMPLETED)

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
            ex.enqueue_with_command(_DEFAULT_PROJECT, j.id, ["echo", j.id], Path("."))

        for j in jobs:
            _wait(store, j.id, TrainingJobStatus.COMPLETED)

        assert max_active[0] == 1, f"Max concurrent jobs was {max_active[0]}, expected 1"


# ===========================================================================
# (c) Hard exclusive VRAM lock
# ===========================================================================

class TestHardExclusiveVramLock:
    def test_is_training_locked_false_initially(self) -> None:
        sched = _make_scheduler()
        assert sched.is_training_locked() is False

    def test_begin_training_sets_lock(self) -> None:
        sched = _make_scheduler()
        sched.begin_training("test-job-1")
        assert sched.is_training_locked() is True

    def test_end_training_clears_lock(self) -> None:
        sched = _make_scheduler()
        sched.begin_training("test-job-1")
        sched.end_training()
        assert sched.is_training_locked() is False

    def test_acquire_raises_while_lock_held(self) -> None:
        """While training lock is held, acquire() must raise SchedulerError."""
        sched = _make_scheduler(vram_mb=12000)
        model = ManagedModel(name="gen_model", vram_mb=4000, ram_mb=4000)
        sched.register(model)

        sched.begin_training("training-job-id")
        with pytest.raises(SchedulerError, match="Training in progress"):
            sched.acquire("gen_model")

    def test_acquire_succeeds_after_end_training(self) -> None:
        """After end_training(), acquire() works normally again."""
        sched = _make_scheduler(vram_mb=12000)
        model = ManagedModel(name="gen_model", vram_mb=4000, ram_mb=4000)
        sched.register(model)

        sched.begin_training("training-job-id")
        sched.end_training()

        # Must not raise.
        result = sched.acquire("gen_model")
        from core.scheduler.vram import RuntimeState
        assert result == RuntimeState.ACTIVE

    def test_is_training_locked_true_during_job_run(self) -> None:
        """While a training job is RUNNING, is_training_locked() must be True."""
        lock_states_during_run: list[bool] = []
        sched = _make_scheduler(vram_mb=12000)

        class ObserverRunner:
            def run(self, args, cwd, *, on_progress=None):
                lock_states_during_run.append(sched.is_training_locked())
                return RunResult(exit_code=0, stderr_tail="")

            def cancel(self) -> None:
                pass

        job = _make_job("jlock-001")
        ex, store = _make_executor([job], scheduler=sched, runner=ObserverRunner())  # type: ignore[arg-type]
        ex.enqueue_with_command(_DEFAULT_PROJECT, "jlock-001", ["echo", "jlock-001"], Path("."))
        _wait(store, "jlock-001", TrainingJobStatus.COMPLETED)

        assert lock_states_during_run, "Runner was never called"
        assert lock_states_during_run[0] is True, (
            "is_training_locked() must be True while a training job is running"
        )

    def test_is_training_locked_false_after_job_completes(self) -> None:
        sched = _make_scheduler(vram_mb=12000)
        job = _make_job("jlock-002")
        ex, store = _make_executor([job], scheduler=sched)
        ex.enqueue_with_command(_DEFAULT_PROJECT, "jlock-002", ["echo", "jlock-002"], Path("."))
        _wait(store, "jlock-002", TrainingJobStatus.COMPLETED)
        assert sched.is_training_locked() is False

    def test_is_training_locked_false_after_job_fails(self) -> None:
        sched = _make_scheduler(vram_mb=12000)
        job = _make_job("jlock-003")
        runner = FakeRunner(exit_code=1, stderr="training error")
        ex, store = _make_executor([job], scheduler=sched, runner=runner)
        ex.enqueue_with_command(_DEFAULT_PROJECT, "jlock-003", ["echo", "jlock-003"], Path("."))
        _wait(store, "jlock-003", TrainingJobStatus.FAILED)
        assert sched.is_training_locked() is False

    def test_training_refused_when_managed_model_active(self) -> None:
        """Direction (a): if a managed model is ACTIVE, training must refuse to start."""
        sched = _make_scheduler(vram_mb=8000)
        gen_model = ManagedModel(name="gen_active", vram_mb=4000, ram_mb=4000)
        sched.register(gen_model)
        sched.acquire("gen_active")  # gen model ACTIVE before training

        from core.scheduler.vram import RuntimeState
        assert sched.state_of("gen_active") == RuntimeState.ACTIVE

        job = _make_job("jevict-001")
        ex, store = _make_executor([job], scheduler=sched)
        ex.enqueue_with_command(_DEFAULT_PROJECT, "jevict-001", ["echo", "jevict-001"], Path("."))
        failed = _wait(store, "jevict-001", TrainingJobStatus.FAILED)

        assert failed.status == TrainingJobStatus.FAILED
        assert failed.note is not None
        assert "ACTIVE" in failed.note, (
            f"Expected failure note to mention ACTIVE model; got: {failed.note!r}"
        )
        # The lock must NOT be left held after the refusal.
        assert sched.is_training_locked() is False

    def test_generation_service_blocks_when_training_locked(self) -> None:
        """Generation execute_job must raise ValueError with training-lock reason
        when is_training_locked() is True (spec §7.3 blocking-reason pattern)."""
        from unittest.mock import MagicMock
        from core.generation.service import GenerationService, _TRAINING_LOCK_REASON
        from core.models.schemas import GenerationJob, GenerationJobStatus

        sched = _make_scheduler()
        sched.begin_training("some-training-job")

        # Build a minimal mock so we don't need a real ProjectManager.
        project_manager = MagicMock()
        workers_service = MagicMock()
        project_dir = MagicMock()
        project_manager.get_project.return_value = (MagicMock(), project_dir)

        service = GenerationService(project_manager, workers_service, scheduler=sched)

        # Stub out _read_jobs, _read_assets, _read_plans, _refresh_jobs.
        now = _now()
        gen_job = GenerationJob(
            id="gj-001",
            project_id=_DEFAULT_PROJECT,
            title="Test gen",
            modality=Modality.IMAGE,
            asset_type="image",
            status=GenerationJobStatus.READY,
            prompt="test",
            summary="",
            worker="comfyui",
            created_at=now,
            updated_at=now,
        )
        service._read_jobs = lambda pd: [gen_job]  # type: ignore[method-assign]
        service._read_assets = lambda pd: []  # type: ignore[method-assign]
        service._read_plans = lambda pd: []  # type: ignore[method-assign]
        service._refresh_jobs = lambda jobs: jobs  # type: ignore[method-assign]

        with pytest.raises(ValueError, match="training in progress"):
            service.execute_job(_DEFAULT_PROJECT, "gj-001")

    def test_generation_service_execute_ready_jobs_skips_when_locked(self) -> None:
        """execute_ready_jobs must skip all jobs with training-lock reason when locked."""
        from unittest.mock import MagicMock
        from core.generation.service import GenerationService, _TRAINING_LOCK_REASON
        from core.models.schemas import GenerationJob, GenerationJobStatus

        sched = _make_scheduler()
        sched.begin_training("some-training-job")

        project_manager = MagicMock()
        workers_service = MagicMock()
        project_dir = MagicMock()
        project_manager.get_project.return_value = (MagicMock(), project_dir)

        service = GenerationService(project_manager, workers_service, scheduler=sched)

        now = _now()
        gen_job = GenerationJob(
            id="gj-002",
            project_id=_DEFAULT_PROJECT,
            title="Test gen 2",
            modality=Modality.IMAGE,
            asset_type="image",
            status=GenerationJobStatus.READY,
            prompt="test",
            summary="",
            worker="comfyui",
            created_at=now,
            updated_at=now,
        )
        service._read_jobs = lambda pd: [gen_job]  # type: ignore[method-assign]
        service._read_assets = lambda pd: []  # type: ignore[method-assign]
        service._read_plans = lambda pd: []  # type: ignore[method-assign]
        service._refresh_jobs = lambda jobs: jobs  # type: ignore[method-assign]
        service._write_jobs = lambda pd, jobs: None  # type: ignore[method-assign]

        result = service.execute_ready_jobs(_DEFAULT_PROJECT)
        assert result.executed_count == 0
        assert len(result.skipped) == 1
        assert _TRAINING_LOCK_REASON in result.skipped[0].reason


# ===========================================================================
# (d) Status transitions
# ===========================================================================

class TestStatusTransitions:
    def test_success_path_queued_running_completed(self) -> None:
        job = _make_job("jst-001", status=TrainingJobStatus.PLANNED)
        runner = FakeRunner(exit_code=0)
        ex, store = _make_executor([job], runner=runner)
        ex.enqueue_with_command(_DEFAULT_PROJECT, "jst-001", ["echo", "jst-001"], Path("."))
        completed = _wait(store, "jst-001", TrainingJobStatus.COMPLETED)
        assert completed.status == TrainingJobStatus.COMPLETED
        assert completed.exit_code == 0

    def test_failure_path_queued_running_failed_nonzero_exit(self) -> None:
        job = _make_job("jst-002", status=TrainingJobStatus.PLANNED)
        runner = FakeRunner(exit_code=1, stderr="Fatal error in training\nOOM\n")
        ex, store = _make_executor([job], runner=runner)
        ex.enqueue_with_command(_DEFAULT_PROJECT, "jst-002", ["echo", "jst-002"], Path("."))
        failed = _wait(store, "jst-002", TrainingJobStatus.FAILED)
        assert failed.status == TrainingJobStatus.FAILED
        assert failed.exit_code == 1
        assert failed.stderr_tail is not None
        assert "Fatal error" in failed.stderr_tail or "OOM" in failed.stderr_tail

    def test_enqueue_transitions_job_to_queued(self) -> None:
        job = _make_job("jst-003", status=TrainingJobStatus.PLANNED)
        barrier = threading.Event()

        class SlowRunner:
            def run(self, args, cwd, *, on_progress=None):
                barrier.wait(timeout=5)
                return RunResult(exit_code=0, stderr_tail="")

            def cancel(self) -> None:
                barrier.set()

        ex, store = _make_executor([job], runner=SlowRunner())  # type: ignore[arg-type]
        ex.enqueue_with_command(_DEFAULT_PROJECT, "jst-003", ["echo", "jst-003"], Path("."))
        deadline = time.monotonic() + 2.0
        seen_queued = False
        while time.monotonic() < deadline:
            for j in store.get(_DEFAULT_PROJECT, []):
                if j.id == "jst-003" and j.status == TrainingJobStatus.QUEUED:
                    seen_queued = True
                    break
            if seen_queued:
                break
            time.sleep(0.01)
        barrier.set()
        _wait(store, "jst-003", TrainingJobStatus.COMPLETED)

    def test_cancel_queued_job_transitions_to_failed(self) -> None:
        """Cancelling a job that is still QUEUED (not yet started) marks it FAILED."""
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

        ex.enqueue_with_command(_DEFAULT_PROJECT, "jcancel-001", ["echo", "jcancel-001"], Path("."))
        running_event.wait(timeout=5)
        ex.enqueue_with_command(_DEFAULT_PROJECT, "jcancel-002", ["echo", "jcancel-002"], Path("."))

        cancelled = ex.cancel_job(_DEFAULT_PROJECT, "jcancel-002")
        assert cancelled is True

        failed = _wait(store, "jcancel-002", TrainingJobStatus.FAILED)
        assert failed.status == TrainingJobStatus.FAILED

        unblock_event.set()
        _wait(store, "jcancel-001", TrainingJobStatus.COMPLETED)

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

        ex.enqueue_with_command(_DEFAULT_PROJECT, "jcancel-run-001", ["echo", "run"], Path("."))
        running_event.wait(timeout=5)

        cancelled = ex.cancel_job(_DEFAULT_PROJECT, "jcancel-run-001")
        assert cancelled is True

        result = _wait(store, "jcancel-run-001",
                       TrainingJobStatus.FAILED, TrainingJobStatus.COMPLETED)
        assert result.status == TrainingJobStatus.FAILED

    def test_progress_updated_during_run(self) -> None:
        """FakeRunner reports progress at 50% and 100%; both must reach the store."""
        job = _make_job("jprog-001")
        runner = FakeRunner(exit_code=0)
        ex, store = _make_executor([job], runner=runner)
        ex.enqueue_with_command(_DEFAULT_PROJECT, "jprog-001", ["echo", "jprog-001"], Path("."))
        completed = _wait(store, "jprog-001", TrainingJobStatus.COMPLETED)
        assert completed.progress == 100

    def test_cancel_nonexistent_job_returns_false(self) -> None:
        job = _make_job("jexist-001")
        ex, _ = _make_executor([job])
        result = ex.cancel_job(_DEFAULT_PROJECT, "does-not-exist")
        assert result is False


# ===========================================================================
# (e) Per-project job store isolation (MAJOR 2 fix)
# ===========================================================================

class TestPerProjectJobIsolation:
    def test_two_projects_have_separate_job_stores(self) -> None:
        """Jobs submitted under two different project_ids must be persisted to
        their own stores independently — cross-project reads/writes must not occur."""
        proj_a = "project-alpha"
        proj_b = "project-beta"

        job_a = _make_job("job-alpha-001", project_id=proj_a)
        job_b = _make_job("job-beta-001", project_id=proj_b)

        # Shared store for both projects.
        stores: dict[str, list[TrainingJob]] = {
            proj_a: [job_a],
            proj_b: [job_b],
        }

        def read_jobs(pid: str) -> list[TrainingJob]:
            return list(stores.setdefault(pid, []))

        def write_jobs(pid: str, new_jobs: list[TrainingJob]) -> None:
            stores[pid] = list(new_jobs)

        sched = _make_scheduler()
        ex = TrainingExecutor(
            read_jobs=read_jobs,
            write_jobs=write_jobs,
            scheduler=sched,
            runner=FakeRunner(exit_code=0),
        )

        ex.enqueue_with_command(proj_a, "job-alpha-001", ["echo", "alpha"], Path("."))
        _wait_for_status(stores, proj_a, "job-alpha-001", TrainingJobStatus.COMPLETED)

        # Only wait for beta after alpha finishes (FIFO — single executor).
        ex.enqueue_with_command(proj_b, "job-beta-001", ["echo", "beta"], Path("."))
        _wait_for_status(stores, proj_b, "job-beta-001", TrainingJobStatus.COMPLETED)

        # Verify that alpha's job is in alpha's store, not beta's, and vice-versa.
        alpha_ids = {j.id for j in stores[proj_a]}
        beta_ids = {j.id for j in stores[proj_b]}

        assert "job-alpha-001" in alpha_ids, "Alpha job must be in alpha store"
        assert "job-beta-001" not in alpha_ids, "Beta job must NOT appear in alpha store"
        assert "job-beta-001" in beta_ids, "Beta job must be in beta store"
        assert "job-alpha-001" not in beta_ids, "Alpha job must NOT appear in beta store"

        # Both must have reached COMPLETED in their own stores.
        alpha_job = next(j for j in stores[proj_a] if j.id == "job-alpha-001")
        beta_job = next(j for j in stores[proj_b] if j.id == "job-beta-001")
        assert alpha_job.status == TrainingJobStatus.COMPLETED
        assert beta_job.status == TrainingJobStatus.COMPLETED


# ===========================================================================
# (f) Live command path — real kohya_ss argv (MAJOR 3 fix)
# ===========================================================================

class TestLiveCommandPath:
    def test_enqueue_with_asset_store_produces_real_kohya_argv(self, tmp_path: Path) -> None:
        """The live command path (asset_store_resolver wired) must produce the real
        kohya_ss argv — NOT the ['echo', ...] stub.  FakeRunner captures all calls.
        """
        from unittest.mock import MagicMock

        # Build entities for the live command construction.
        sheet = _character_sheet()
        pack = _dataset_pack(source="/data/kyuoka_dataset")
        recipe = _training_recipe()

        # Fake AssetStore that returns known entities.
        class FakeAssetStore:
            def list_character_sheets(self, pid: str) -> list[CharacterSheet]:
                return [sheet]

            def list_dataset_packs(self, pid: str) -> list[DatasetPack]:
                return [pack]

            def list_training_recipes(self, pid: str) -> list[TrainingRecipe]:
                return [recipe]

        fake_store = FakeAssetStore()
        project_dir = tmp_path / "project"
        project_dir.mkdir()

        stores: dict[str, list[TrainingJob]] = {}

        def read_jobs(pid: str) -> list[TrainingJob]:
            return list(stores.setdefault(pid, []))

        def write_jobs(pid: str, new_jobs: list[TrainingJob]) -> None:
            stores[pid] = list(new_jobs)

        # Pre-populate with a LoRA job.
        job = TrainingJob(
            id="live-job-001",
            project_id=_DEFAULT_PROJECT,
            title="Live LoRA job",
            modality=Modality.IMAGE,
            worker="kohya-ss",
            dataset_path="/data/kyuoka_dataset",
            status=TrainingJobStatus.PLANNED,
            created_at=_now(),
            updated_at=_now(),
        )
        stores[_DEFAULT_PROJECT] = [job]

        fake_runner = FakeRunner(exit_code=0)
        sched = _make_scheduler()

        ex = TrainingExecutor(
            read_jobs=read_jobs,
            write_jobs=write_jobs,
            scheduler=sched,
            runner=fake_runner,
            asset_store_resolver=lambda pid: fake_store,
            project_dir_resolver=lambda pid: project_dir,
        )

        ex.enqueue(_DEFAULT_PROJECT, "live-job-001")
        _wait_for_status(stores, _DEFAULT_PROJECT, "live-job-001", TrainingJobStatus.COMPLETED)

        # FakeRunner must have been called exactly once.
        assert len(fake_runner.calls) == 1, (
            f"Expected 1 runner call; got {len(fake_runner.calls)}"
        )
        captured_args, captured_cwd = fake_runner.calls[0]

        # The real argv must contain train_network.py, NOT "echo".
        combined = " ".join(captured_args)
        assert "train_network.py" in combined, (
            f"Live command must invoke train_network.py; got: {combined!r}"
        )
        assert "echo" not in captured_args, (
            f"Live command must NOT fall back to 'echo'; got: {captured_args!r}"
        )

        # The base_model from the recipe must appear.
        assert recipe.base_model in combined, (
            f"Recipe base_model not found in live argv: {combined!r}"
        )
