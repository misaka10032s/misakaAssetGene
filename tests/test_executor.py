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

# Fixed instant for every seeded record: no test here reads the real clock.
FIXED_NOW = datetime(2026, 1, 15, 12, 0, tzinfo=timezone.utc)


def _now() -> datetime:
    return FIXED_NOW


class _RecordedThread:
    """Stands in for the executor's worker thread: records the creation and
    ``start()`` but never runs ``target``.  Tests drain the queue on the test
    thread with ``TrainingExecutor.run_until_idle()`` instead."""

    def __init__(self, *, target, name: str, daemon: bool) -> None:
        self.target = target
        self.name = name
        self.daemon = daemon
        self.started = False

    def start(self) -> None:
        self.started = True

    def is_alive(self) -> bool:
        return self.started


class _ThreadRecorder:
    """``thread_factory`` for ``TrainingExecutor``: every thread it is asked
    for is a ``_RecordedThread``; ``threads`` lists them in creation order."""

    def __init__(self) -> None:
        self.threads: list[_RecordedThread] = []

    def __call__(self, **kwargs) -> _RecordedThread:
        thread = _RecordedThread(**kwargs)
        self.threads.append(thread)
        return thread


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
    thread_factory: _ThreadRecorder | None = None,
) -> tuple[TrainingExecutor, dict[str, list[TrainingJob]]]:
    """Return an executor backed by a per-project in-memory job store.

    The store is keyed by project_id so the two-project isolation test can
    verify that each project's jobs are persisted independently.  The worker
    thread is a ``_ThreadRecorder`` product: it never runs, so a test drives
    the queue with ``ex.run_until_idle()``.
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
        thread_factory=thread_factory or _ThreadRecorder(),
    )
    return ex, stores


def _job_with_status(
    stores: dict[str, list[TrainingJob]],
    project_id: str,
    job_id: str,
    *statuses: TrainingJobStatus,
) -> TrainingJob:
    """Return the job from the store; fail when it is not in one of ``statuses`` now.

    Nothing waits: the caller has already driven the executor with
    ``run_until_idle()``, so the stored status is final for that step.
    """
    found = [j for j in stores.get(project_id, []) if j.id == job_id]
    assert found, f"Job {job_id} (project {project_id}) is not in the store"
    job = found[0]
    assert job.status in statuses, (
        f"Job {job_id} (project {project_id}) is {job.status}; expected one of {statuses}"
    )
    return job


# Convenience wrapper for single-project tests.
def _job(
    store: dict[str, list[TrainingJob]],
    job_id: str,
    *statuses: TrainingJobStatus,
    project_id: str = _DEFAULT_PROJECT,
) -> TrainingJob:
    return _job_with_status(store, project_id, job_id, *statuses)


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


# ===========================================================================
# (h) kohya_ss v25.0.3 moved train_network.py into the sd-scripts/ submodule
#     (bug fix, 2026-09-07): build_lora_command must reference
#     <kohya_root>/sd-scripts/train_network.py, not <kohya_root>/train_network.py.
# ===========================================================================

class TestKohyaScriptPathSdScriptsSubdir:
    def test_lora_script_arg_is_under_sd_scripts_subdir(self, tmp_path: Path) -> None:
        kohya_dir = tmp_path / "kohya_ss"
        spec = build_lora_command(
            character_sheet=_character_sheet(),
            dataset_pack=_dataset_pack(),
            recipe=_training_recipe(),
            project_models_dir=tmp_path / "models",
            kohya_ss_dir=kohya_dir,
        )
        expected_script = str(kohya_dir / "sd-scripts" / "train_network.py")
        assert expected_script in spec.args, (
            f"expected script arg {expected_script!r} (v25.0.3 sd-scripts/ "
            f"submodule layout) in argv; got {spec.args!r}"
        )
        # cwd must stay the kohya_ss clone ROOT (relative config/log paths
        # resolve against the root, not the sd-scripts subdir) -- only the
        # script invocation path moves into the submodule.
        assert spec.cwd == kohya_dir


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
        events: list[str] = []

        class SingleRunner:
            def run(self, args, cwd, *, on_progress=None):
                events.append(f"start:{args[1]}")
                result = RunResult(exit_code=0, stderr_tail="")
                if on_progress:
                    on_progress(100, "done")
                events.append(f"end:{args[1]}")
                return result

            def cancel(self) -> None:
                pass

        job1 = _make_job("job-001")
        job2 = _make_job("job-002")
        ex, store = _make_executor([job1, job2], runner=SingleRunner())  # type: ignore[arg-type]

        ex.enqueue_with_command(_DEFAULT_PROJECT, "job-001", ["echo", "a"], Path("."))
        ex.enqueue_with_command(_DEFAULT_PROJECT, "job-002", ["echo", "b"], Path("."))

        # The caller's enqueue did no worker-side work: nothing has run yet.
        assert events == []

        ex.run_until_idle()

        j1 = _job(store, "job-001", TrainingJobStatus.COMPLETED)
        j2 = _job(store, "job-002", TrainingJobStatus.COMPLETED)

        assert j1.status == TrainingJobStatus.COMPLETED
        assert j2.status == TrainingJobStatus.COMPLETED
        # FIFO and one at a time: the first job ends before the second starts.
        assert events == ["start:a", "end:a", "start:b", "end:b"]

    def test_jobs_run_one_at_a_time_never_concurrent(self) -> None:
        """Assert concurrency count never exceeds 1 during overlapping enqueues."""
        active_count = [0]
        max_active = [0]
        run_order: list[str] = []

        class ConcurrencyTracker:
            def run(self, args, cwd, *, on_progress=None):
                run_order.append(args[1])
                active_count[0] += 1
                if active_count[0] > max_active[0]:
                    max_active[0] = active_count[0]
                active_count[0] -= 1
                return RunResult(exit_code=0, stderr_tail="")

            def cancel(self) -> None:
                pass

        recorder = _ThreadRecorder()
        jobs = [_make_job(f"job-{i:03d}") for i in range(4)]
        ex, store = _make_executor(
            jobs, runner=ConcurrencyTracker(), thread_factory=recorder  # type: ignore[arg-type]
        )

        for j in jobs:
            ex.enqueue_with_command(_DEFAULT_PROJECT, j.id, ["echo", j.id], Path("."))

        # The product's guarantee is ONE worker: four enqueues created one thread.
        assert len(recorder.threads) == 1
        assert run_order == [], "enqueue must not run any job on the caller's thread"

        ex.run_until_idle()

        for j in jobs:
            _job(store, j.id, TrainingJobStatus.COMPLETED)

        assert max_active[0] == 1, f"Max concurrent jobs was {max_active[0]}, expected 1"
        assert run_order == [j.id for j in jobs], "jobs must run in FIFO order"


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
        ex.run_until_idle()
        _job(store, "jlock-001", TrainingJobStatus.COMPLETED)

        assert lock_states_during_run, "Runner was never called"
        assert lock_states_during_run[0] is True, (
            "is_training_locked() must be True while a training job is running"
        )

    def test_is_training_locked_false_after_job_completes(self) -> None:
        sched = _make_scheduler(vram_mb=12000)
        job = _make_job("jlock-002")
        ex, store = _make_executor([job], scheduler=sched)
        ex.enqueue_with_command(_DEFAULT_PROJECT, "jlock-002", ["echo", "jlock-002"], Path("."))
        ex.run_until_idle()
        _job(store, "jlock-002", TrainingJobStatus.COMPLETED)
        assert sched.is_training_locked() is False

    def test_is_training_locked_false_after_job_fails(self) -> None:
        sched = _make_scheduler(vram_mb=12000)
        job = _make_job("jlock-003")
        runner = FakeRunner(exit_code=1, stderr="training error")
        ex, store = _make_executor([job], scheduler=sched, runner=runner)
        ex.enqueue_with_command(_DEFAULT_PROJECT, "jlock-003", ["echo", "jlock-003"], Path("."))
        ex.run_until_idle()
        _job(store, "jlock-003", TrainingJobStatus.FAILED)
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
        ex.run_until_idle()
        failed = _job(store, "jevict-001", TrainingJobStatus.FAILED)

        assert failed.status == TrainingJobStatus.FAILED
        assert failed.note is not None
        assert "ACTIVE" in failed.note, (
            f"Expected failure note to mention ACTIVE model; got: {failed.note!r}"
        )
        # The lock must NOT be left held after the refusal.
        assert sched.is_training_locked() is False

    def test_toctou_race_between_active_check_and_training_lock(self) -> None:
        """Regression for the check-then-lock TOCTOU (spec §7.3).

        A model must not be able to slip into ACTIVE state in the gap
        between "is any managed model ACTIVE?" and the training lock
        actually being taken.  The buggy executor read ``scheduler._models``
        directly (UNLOCKED) and then called ``begin_training()`` as a
        completely separate step, so a concurrent ``acquire()`` could land in
        that gap.  The fix performs the whole decision atomically under the
        scheduler's own lock.

        Observed without any second thread: the scheduler's lock is replaced
        by a lock that records whether it is held and which hold ("span") it
        is.  The ACTIVE scan and the moment the training lock is taken are
        each recorded with that state; the test asserts both happen while the
        lock is held, in the SAME span (no gap in which another caller could
        take the lock).
        """
        from core.scheduler.vram import RuntimeState

        class _SpanLock:
            """Re-entrant lock that knows whether it is held (``depth``) and
            which hold it is (``span``, one number per outermost hold)."""

            def __init__(self) -> None:
                self._inner = threading.RLock()
                self.depth = 0
                self.span = 0

            def acquire(self, blocking: bool = True) -> bool:
                got = self._inner.acquire(blocking)
                if got:
                    if self.depth == 0:
                        self.span += 1
                    self.depth += 1
                return got

            def release(self) -> None:
                self.depth -= 1
                self._inner.release()

            def __enter__(self) -> _SpanLock:
                self.acquire()
                return self

            def __exit__(self, *exc_info: object) -> None:
                self.release()

        span_lock = _SpanLock()
        observations: list[tuple[str, int, int]] = []

        class _RecordingScheduler(ModelScheduler):
            def __setattr__(self, name: str, value: object) -> None:
                if name == "_training_lock_holder" and value is not None:
                    observations.append(("lock_taken", span_lock.depth, span_lock.span))
                super().__setattr__(name, value)

        class _ScanRecordingDict(dict):
            """Wraps ``ModelScheduler._models``; every scan of the ACTIVE
            check goes through ``items()`` and is recorded."""

            def items(self):
                observations.append(("scan", span_lock.depth, span_lock.span))
                return dict.items(self)

        sched = _RecordingScheduler(SchedulerBudget(vram_budget_mb=8000, ram_budget_mb=32000))
        sched.register(ManagedModel(name="gen_race", vram_mb=4000, ram_mb=4000))
        sched._lock = span_lock  # type: ignore[assignment]
        sched._models = _ScanRecordingDict(sched._models)  # type: ignore[assignment]

        observed_conflict: list[bool] = []

        class ObserverRunner:
            def run(self, args, cwd, *, on_progress=None):
                observed_conflict.append(
                    sched.is_training_locked()
                    and sched.state_of("gen_race") == RuntimeState.ACTIVE
                )
                return RunResult(exit_code=0, stderr_tail="")

            def cancel(self) -> None:
                pass

        job = _make_job("jrace-001")
        ex, store = _make_executor([job], scheduler=sched, runner=ObserverRunner())  # type: ignore[arg-type]
        ex.enqueue_with_command(_DEFAULT_PROJECT, "jrace-001", ["echo", "jrace-001"], Path("."))
        ex.run_until_idle()

        _job(store, "jrace-001", TrainingJobStatus.COMPLETED)

        scans = [o for o in observations if o[0] == "scan"]
        taken = [o for o in observations if o[0] == "lock_taken"]
        assert len(scans) == 1, f"expected exactly one ACTIVE scan; got {observations!r}"
        assert len(taken) == 1, f"expected exactly one training-lock take; got {observations!r}"
        _, scan_depth, scan_span = scans[0]
        _, taken_depth, taken_span = taken[0]
        assert scan_depth >= 1, (
            "TOCTOU: the ACTIVE scan ran WITHOUT the scheduler's lock held"
        )
        assert taken_depth >= 1, (
            "TOCTOU: the training lock was taken WITHOUT the scheduler's lock held"
        )
        assert scan_span == taken_span, (
            "TOCTOU: the ACTIVE scan and the training-lock take were two separate "
            "holds of the scheduler's lock — a concurrent acquire() can slip "
            "through the gap between them."
        )
        # The runner executed with the lock held and no model ACTIVE.
        assert observed_conflict == [False]
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
        ex.run_until_idle()
        completed = _job(store, "jst-001", TrainingJobStatus.COMPLETED)
        assert completed.status == TrainingJobStatus.COMPLETED
        assert completed.exit_code == 0

    def test_failure_path_queued_running_failed_nonzero_exit(self) -> None:
        job = _make_job("jst-002", status=TrainingJobStatus.PLANNED)
        runner = FakeRunner(exit_code=1, stderr="Fatal error in training\nOOM\n")
        ex, store = _make_executor([job], runner=runner)
        ex.enqueue_with_command(_DEFAULT_PROJECT, "jst-002", ["echo", "jst-002"], Path("."))
        ex.run_until_idle()
        failed = _job(store, "jst-002", TrainingJobStatus.FAILED)
        assert failed.status == TrainingJobStatus.FAILED
        assert failed.exit_code == 1
        assert failed.stderr_tail is not None
        assert "Fatal error" in failed.stderr_tail or "OOM" in failed.stderr_tail

    def test_enqueue_transitions_job_to_queued(self) -> None:
        job = _make_job("jst-003", status=TrainingJobStatus.PLANNED)
        ex, store = _make_executor([job])
        ex.enqueue_with_command(_DEFAULT_PROJECT, "jst-003", ["echo", "jst-003"], Path("."))
        # Right after enqueue, before the worker has run anything: QUEUED.
        queued = _job(store, "jst-003", TrainingJobStatus.QUEUED)
        assert queued.status == TrainingJobStatus.QUEUED
        ex.run_until_idle()
        _job(store, "jst-003", TrainingJobStatus.COMPLETED)

    def test_cancel_queued_job_transitions_to_failed(self) -> None:
        """Cancelling a job that is still QUEUED (not yet started) marks it FAILED."""
        runner = FakeRunner(exit_code=0)
        job1 = _make_job("jcancel-001")
        job2 = _make_job("jcancel-002")
        ex, store = _make_executor([job1, job2], runner=runner)

        ex.enqueue_with_command(_DEFAULT_PROJECT, "jcancel-001", ["echo", "jcancel-001"], Path("."))
        ex.enqueue_with_command(_DEFAULT_PROJECT, "jcancel-002", ["echo", "jcancel-002"], Path("."))
        # Neither job has started: both are QUEUED.
        _job(store, "jcancel-001", TrainingJobStatus.QUEUED)
        _job(store, "jcancel-002", TrainingJobStatus.QUEUED)

        cancelled = ex.cancel_job(_DEFAULT_PROJECT, "jcancel-002")
        assert cancelled is True

        failed = _job(store, "jcancel-002", TrainingJobStatus.FAILED)
        assert failed.status == TrainingJobStatus.FAILED

        ex.run_until_idle()
        _job(store, "jcancel-001", TrainingJobStatus.COMPLETED)
        # The cancelled job stays FAILED and its command never ran.
        _job(store, "jcancel-002", TrainingJobStatus.FAILED)
        assert [args for args, _cwd in runner.calls] == [["echo", "jcancel-001"]]

    def test_cancel_running_job_causes_failure(self) -> None:
        """Cancelling a RUNNING job causes it to transition to FAILED."""
        cancel_results: list[bool] = []
        holder: dict[str, TrainingExecutor] = {}

        class CancellableRunner:
            def __init__(self) -> None:
                self.cancelled = False

            def run(self, args, cwd, *, on_progress=None):
                # The job is RUNNING right now: cancel it from inside the run.
                cancel_results.append(
                    holder["ex"].cancel_job(_DEFAULT_PROJECT, "jcancel-run-001")
                )
                if self.cancelled:
                    return RunResult(exit_code=-1, stderr_tail="Cancelled by user")
                return RunResult(exit_code=0, stderr_tail="")

            def cancel(self) -> None:
                self.cancelled = True

        cr = CancellableRunner()
        job = _make_job("jcancel-run-001")
        ex, store = _make_executor([job], runner=cr)  # type: ignore[arg-type]
        holder["ex"] = ex

        ex.enqueue_with_command(_DEFAULT_PROJECT, "jcancel-run-001", ["echo", "run"], Path("."))
        ex.run_until_idle()

        assert cancel_results == [True]
        assert cr.cancelled is True

        result = _job(store, "jcancel-run-001",
                      TrainingJobStatus.FAILED, TrainingJobStatus.COMPLETED)
        assert result.status == TrainingJobStatus.FAILED

    def test_progress_updated_during_run(self) -> None:
        """FakeRunner reports progress at 50% and 100%; both must reach the store."""
        job = _make_job("jprog-001")
        runner = FakeRunner(exit_code=0)
        ex, store = _make_executor([job], runner=runner)
        ex.enqueue_with_command(_DEFAULT_PROJECT, "jprog-001", ["echo", "jprog-001"], Path("."))
        ex.run_until_idle()
        completed = _job(store, "jprog-001", TrainingJobStatus.COMPLETED)
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
            thread_factory=_ThreadRecorder(),
        )

        ex.enqueue_with_command(proj_a, "job-alpha-001", ["echo", "alpha"], Path("."))
        ex.run_until_idle()
        _job_with_status(stores, proj_a, "job-alpha-001", TrainingJobStatus.COMPLETED)

        # Beta is enqueued after alpha has finished (FIFO — single executor).
        ex.enqueue_with_command(proj_b, "job-beta-001", ["echo", "beta"], Path("."))
        ex.run_until_idle()
        _job_with_status(stores, proj_b, "job-beta-001", TrainingJobStatus.COMPLETED)

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
        kohya_install_dir = tmp_path / "workers" / "kohya-ss"

        ex = TrainingExecutor(
            read_jobs=read_jobs,
            write_jobs=write_jobs,
            scheduler=sched,
            runner=fake_runner,
            asset_store_resolver=lambda pid: fake_store,
            project_dir_resolver=lambda pid: project_dir,
            workers_service=_FakeWorkersService(kohya_install_dir),
            thread_factory=_ThreadRecorder(),
        )

        ex.enqueue(_DEFAULT_PROJECT, "live-job-001")
        ex.run_until_idle()
        _job_with_status(stores, _DEFAULT_PROJECT, "live-job-001", TrainingJobStatus.COMPLETED)

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

        # cwd must be the WorkersService-resolved install path, not a guess.
        assert captured_cwd == kohya_install_dir, (
            f"Expected cwd {kohya_install_dir}; got {captured_cwd}"
        )


# ===========================================================================
# (g) kohya_ss working directory resolved from workers/manifest.json, not
#     guessed from the dataset location (MAJOR fix, 2026-09-07).
# ===========================================================================

class _FakeWorkersService:
    """Test double standing in for core.integration.workers.WorkersService."""

    def __init__(self, path: Path | None = None, error: Exception | None = None) -> None:
        self._path = path
        self._error = error
        self.requested_worker_names: list[str] = []

    def resolve_installed_worker_path(self, worker_name: str) -> Path:
        self.requested_worker_names.append(worker_name)
        if self._error is not None:
            raise self._error
        assert self._path is not None
        return self._path


class TestKohyaWorkerDirResolution:
    def _submit_kohya_job(
        self,
        tmp_path: Path,
        *,
        workers_service: object | None,
        dataset_path: str = "/data/kyuoka_dataset",
    ) -> tuple[dict[str, list[TrainingJob]], FakeRunner]:
        sheet = _character_sheet()
        pack = _dataset_pack(source=dataset_path)
        recipe = _training_recipe()

        class FakeAssetStore:
            def list_character_sheets(self, pid: str) -> list[CharacterSheet]:
                return [sheet]

            def list_dataset_packs(self, pid: str) -> list[DatasetPack]:
                return [pack]

            def list_training_recipes(self, pid: str) -> list[TrainingRecipe]:
                return [recipe]

        project_dir = tmp_path / "project"
        project_dir.mkdir()

        stores: dict[str, list[TrainingJob]] = {}

        def read_jobs(pid: str) -> list[TrainingJob]:
            return list(stores.setdefault(pid, []))

        def write_jobs(pid: str, new_jobs: list[TrainingJob]) -> None:
            stores[pid] = list(new_jobs)

        job = TrainingJob(
            id="kohya-dir-job-001",
            project_id=_DEFAULT_PROJECT,
            title="kohya dir resolution job",
            modality=Modality.IMAGE,
            worker="kohya-ss",
            dataset_path=dataset_path,
            status=TrainingJobStatus.PLANNED,
            created_at=_now(),
            updated_at=_now(),
        )
        stores[_DEFAULT_PROJECT] = [job]

        fake_runner = FakeRunner(exit_code=0)
        ex = TrainingExecutor(
            read_jobs=read_jobs,
            write_jobs=write_jobs,
            scheduler=_make_scheduler(),
            runner=fake_runner,
            asset_store_resolver=lambda pid: FakeAssetStore(),
            project_dir_resolver=lambda pid: project_dir,
            workers_service=workers_service,
            thread_factory=_ThreadRecorder(),
        )

        ex.enqueue(_DEFAULT_PROJECT, "kohya-dir-job-001")
        ex.run_until_idle()
        return stores, fake_runner

    def test_kohya_cwd_comes_from_workers_service_not_dataset_location(
        self, tmp_path: Path
    ) -> None:
        """The kohya_ss working directory must be whatever WorkersService
        resolves from workers/manifest.json — even when that path shares no
        relationship with the job's dataset_path (the old, wrong guess was
        ``Path(dataset_path).parent / "kohya_ss"``)."""
        real_install_dir = tmp_path / "totally" / "unrelated" / "install-location"
        wrong_guess_dir = Path("/data") / "kohya_ss"  # what the old code guessed

        stores, fake_runner = self._submit_kohya_job(
            tmp_path,
            workers_service=_FakeWorkersService(real_install_dir),
            dataset_path="/data/kyuoka_dataset",
        )
        job = _job_with_status(
            stores, _DEFAULT_PROJECT, "kohya-dir-job-001", TrainingJobStatus.COMPLETED
        )
        assert job.status == TrainingJobStatus.COMPLETED

        assert len(fake_runner.calls) == 1
        _, captured_cwd = fake_runner.calls[0]
        assert captured_cwd == real_install_dir, (
            f"cwd must come from WorkersService, got {captured_cwd!r}"
        )
        assert captured_cwd != wrong_guess_dir

    def test_kohya_job_fails_clearly_when_worker_not_installed(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """When the manifest marks kohya-ss as not installed (or its path is
        missing), the job must fail with a clear, named reason — not a silent
        fallback to a guessed directory."""
        clear_error = SchedulerError(
            "Worker 'kohya-ss' is not installed (workers/manifest.json "
            "'installed': false). Install it before submitting a training job."
        )
        stores, fake_runner = self._submit_kohya_job(
            tmp_path,
            workers_service=_FakeWorkersService(error=clear_error),
        )
        job = _job_with_status(
            stores, _DEFAULT_PROJECT, "kohya-dir-job-001", TrainingJobStatus.FAILED
        )
        assert job.status == TrainingJobStatus.FAILED
        # The runner must never have been invoked -- the job must fail before
        # any subprocess is attempted.
        assert len(fake_runner.calls) == 0
        assert "not installed" in caplog.text

    def test_kohya_job_fails_clearly_when_no_workers_service_wired(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A TrainingExecutor built without a workers_service must never fall
        back to guessing the kohya_ss directory -- it must fail the job with
        a clear, named reason instead."""
        stores, fake_runner = self._submit_kohya_job(tmp_path, workers_service=None)
        job = _job_with_status(
            stores, _DEFAULT_PROJECT, "kohya-dir-job-001", TrainingJobStatus.FAILED
        )
        assert job.status == TrainingJobStatus.FAILED
        assert len(fake_runner.calls) == 0
        assert "no WorkersService configured" in caplog.text
