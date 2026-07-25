"""Contract tests for §7.3 resume-from-checkpoint (kohya_ss LoRA only).

All tests run without a real kohya_ss install or GPU.  They exercise:
  (R1) Initial submit: --save_state present; --save_every_n_epochs present.
  (R2) Fresh (non-resume) submit: --resume must NOT appear.
  (R3) Resume submit: --resume <checkpoint_dir> appended when resume_checkpoint_path given.
  (R4) Path discovery: _discover_resume_checkpoint() picks the correct state dir.
  (R5) Executor integration: on job failure, sets resume_checkpoint_path when state dirs exist.
"""

from __future__ import annotations

import time
import threading
from pathlib import Path
from datetime import datetime, timezone

import pytest

from core.models.schemas import (
    CharacterSheet,
    DatasetPack,
    Modality,
    TrainingJob,
    TrainingJobStatus,
    TrainingRecipe,
)
from core.scheduler.vram import ModelScheduler, SchedulerBudget
from core.training.executor import FakeRunner, RunResult, TrainingExecutor, _discover_resume_checkpoint
from core.training.lora import build_lora_command


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_DEFAULT_PROJECT = "proj-resume-001"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _character_sheet() -> CharacterSheet:
    return CharacterSheet(
        id="cs-r01",
        project_id=_DEFAULT_PROJECT,
        name="Kyuoka",
        visual_anchors=[],
        trigger_words=["kyuoka_char"],
        forbidden_features=[],
        reference_image_refs=[],
        created_at=_now(),
        updated_at=_now(),
    )


def _dataset_pack(source: str = "/data/kyuoka_dataset") -> DatasetPack:
    return DatasetPack(
        id="dp-r01",
        project_id=_DEFAULT_PROJECT,
        source=source,
        cleaning_status="cleaned",
        tags=[],
        license="cc0",
        split_strategy="80_20",
        members=[],
        created_at=_now(),
        updated_at=_now(),
    )


def _training_recipe() -> TrainingRecipe:
    return TrainingRecipe(
        id="tr-r01",
        project_id=_DEFAULT_PROJECT,
        base_model="stabilityai/stable-diffusion-xl-base-1.0",
        rank=32,
        epochs=10,
        optimizer="AdamW8bit",
        caption_strategy="wd14",
        created_at=_now(),
        updated_at=_now(),
    )


def _make_job(job_id: str = "job-r001") -> TrainingJob:
    return TrainingJob(
        id=job_id,
        project_id=_DEFAULT_PROJECT,
        title="Resume test job",
        modality=Modality.IMAGE,
        worker="kohya-ss",
        dataset_path="/data/kyuoka_dataset",
        status=TrainingJobStatus.PLANNED,
        created_at=_now(),
        updated_at=_now(),
    )


def _make_scheduler() -> ModelScheduler:
    return ModelScheduler(SchedulerBudget(vram_budget_mb=12000, ram_budget_mb=32000))


def _make_executor(
    jobs: list[TrainingJob],
    *,
    runner: FakeRunner | None = None,
) -> tuple[TrainingExecutor, dict[str, list[TrainingJob]]]:
    stores: dict[str, list[TrainingJob]] = {_DEFAULT_PROJECT: list(jobs)}

    def read_jobs(pid: str) -> list[TrainingJob]:
        return list(stores.setdefault(pid, []))

    def write_jobs(pid: str, new_jobs: list[TrainingJob]) -> None:
        stores[pid] = list(new_jobs)

    sched = _make_scheduler()
    fake = runner or FakeRunner(exit_code=0)
    ex = TrainingExecutor(
        read_jobs=read_jobs,
        write_jobs=write_jobs,
        scheduler=sched,
        runner=fake,
    )
    return ex, stores


def _wait(
    stores: dict[str, list[TrainingJob]],
    job_id: str,
    *statuses: TrainingJobStatus,
    timeout: float = 5.0,
) -> TrainingJob:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for j in stores.get(_DEFAULT_PROJECT, []):
            if j.id == job_id and j.status in statuses:
                return j
        time.sleep(0.02)
    found = [j.status for j in stores.get(_DEFAULT_PROJECT, []) if j.id == job_id]
    raise TimeoutError(
        f"Job {job_id} did not reach {statuses} within {timeout}s; current: {found}"
    )


# ===========================================================================
# (R1) Initial submit: --save_state + --save_every_n_epochs must be present
# ===========================================================================

class TestSaveStateArgvContract:
    def test_lora_args_contain_save_state_flag(self, tmp_path: Path) -> None:
        """--save_state (bare flag) must be present in every initial LoRA argv."""
        spec = build_lora_command(
            character_sheet=_character_sheet(),
            dataset_pack=_dataset_pack(),
            recipe=_training_recipe(),
            project_models_dir=tmp_path / "models",
            kohya_ss_dir=tmp_path / "kohya_ss",
        )
        assert "--save_state" in spec.args, (
            f"--save_state must be present in LoRA argv; got: {spec.args!r}"
        )

    def test_lora_args_contain_save_every_n_epochs(self, tmp_path: Path) -> None:
        """--save_every_n_epochs=<N> must be present so --save_state has a cadence."""
        spec = build_lora_command(
            character_sheet=_character_sheet(),
            dataset_pack=_dataset_pack(),
            recipe=_training_recipe(),
            project_models_dir=tmp_path / "models",
            kohya_ss_dir=tmp_path / "kohya_ss",
        )
        combined = " ".join(spec.args)
        assert "--save_every_n_epochs=" in combined, (
            f"--save_every_n_epochs must be present alongside --save_state; got: {combined!r}"
        )

    def test_save_every_n_epochs_is_positive_integer(self, tmp_path: Path) -> None:
        """The value of --save_every_n_epochs must be a positive integer."""
        spec = build_lora_command(
            character_sheet=_character_sheet(),
            dataset_pack=_dataset_pack(),
            recipe=_training_recipe(),
            project_models_dir=tmp_path / "models",
            kohya_ss_dir=tmp_path / "kohya_ss",
        )
        n_arg = next(
            (a for a in spec.args if a.startswith("--save_every_n_epochs=")),
            None,
        )
        assert n_arg is not None, "--save_every_n_epochs arg not found"
        _, _, val = n_arg.partition("=")
        assert val.isdigit() and int(val) >= 1, (
            f"--save_every_n_epochs value must be a positive integer; got: {val!r}"
        )


# ===========================================================================
# (R2) Fresh submit: --resume must NOT be in the argv
# ===========================================================================

class TestNoResumeOnFreshSubmit:
    def test_lora_args_no_resume_flag_by_default(self, tmp_path: Path) -> None:
        """On a fresh (non-resume) submit, --resume must NOT appear in the argv."""
        spec = build_lora_command(
            character_sheet=_character_sheet(),
            dataset_pack=_dataset_pack(),
            recipe=_training_recipe(),
            project_models_dir=tmp_path / "models",
            kohya_ss_dir=tmp_path / "kohya_ss",
        )
        combined = " ".join(spec.args)
        assert "--resume" not in combined, (
            f"--resume must NOT appear on a fresh submit; got: {combined!r}"
        )

    def test_lora_args_no_resume_flag_when_none(self, tmp_path: Path) -> None:
        """Explicitly passing resume_checkpoint_path=None must also produce no --resume."""
        spec = build_lora_command(
            character_sheet=_character_sheet(),
            dataset_pack=_dataset_pack(),
            recipe=_training_recipe(),
            project_models_dir=tmp_path / "models",
            kohya_ss_dir=tmp_path / "kohya_ss",
            resume_checkpoint_path=None,
        )
        combined = " ".join(spec.args)
        assert "--resume" not in combined, (
            f"--resume must NOT appear when resume_checkpoint_path=None; got: {combined!r}"
        )


# ===========================================================================
# (R3) Resume submit: --resume <checkpoint_dir> must be appended
# ===========================================================================

class TestResumeArgvContract:
    def test_lora_args_contain_resume_when_path_given(self, tmp_path: Path) -> None:
        """When resume_checkpoint_path is set, --resume <dir> must appear in the argv."""
        checkpoint_dir = tmp_path / "output" / "mylora-state"
        spec = build_lora_command(
            character_sheet=_character_sheet(),
            dataset_pack=_dataset_pack(),
            recipe=_training_recipe(),
            project_models_dir=tmp_path / "models",
            kohya_ss_dir=tmp_path / "kohya_ss",
            resume_checkpoint_path=checkpoint_dir,
        )
        combined = " ".join(spec.args)
        assert "--resume" in combined, (
            f"--resume must appear when resume_checkpoint_path is set; got: {combined!r}"
        )
        assert str(checkpoint_dir) in combined, (
            f"The checkpoint_dir path must appear after --resume; got: {combined!r}"
        )

    def test_lora_resume_flag_is_separate_arg(self, tmp_path: Path) -> None:
        """--resume and its value must be two consecutive separate args (not key=value)."""
        checkpoint_dir = tmp_path / "out" / "run-state"
        spec = build_lora_command(
            character_sheet=_character_sheet(),
            dataset_pack=_dataset_pack(),
            recipe=_training_recipe(),
            project_models_dir=tmp_path / "models",
            kohya_ss_dir=tmp_path / "kohya_ss",
            resume_checkpoint_path=checkpoint_dir,
        )
        # --resume must appear as a standalone arg, followed by the path as the next arg
        try:
            idx = spec.args.index("--resume")
        except ValueError:
            pytest.fail(f"--resume not found as a standalone arg; args={spec.args!r}")
        assert idx + 1 < len(spec.args), "--resume arg must be followed by the path arg"
        assert spec.args[idx + 1] == str(checkpoint_dir), (
            f"Arg after --resume must be the checkpoint path; "
            f"got {spec.args[idx + 1]!r}, expected {str(checkpoint_dir)!r}"
        )

    def test_lora_resume_path_absolute_is_preserved(self, tmp_path: Path) -> None:
        """The checkpoint path passed to --resume must be preserved exactly as given."""
        checkpoint_dir = tmp_path / "models" / "run_lora-stateNNNNNN"
        spec = build_lora_command(
            character_sheet=_character_sheet(),
            dataset_pack=_dataset_pack(),
            recipe=_training_recipe(),
            project_models_dir=tmp_path / "models",
            kohya_ss_dir=tmp_path / "kohya_ss",
            resume_checkpoint_path=checkpoint_dir,
        )
        idx = spec.args.index("--resume")
        assert spec.args[idx + 1] == str(checkpoint_dir)

    def test_lora_save_state_present_on_resume_submit_too(self, tmp_path: Path) -> None:
        """--save_state must also be present on a resume submit (so next resume is possible)."""
        checkpoint_dir = tmp_path / "out" / "lora-state"
        spec = build_lora_command(
            character_sheet=_character_sheet(),
            dataset_pack=_dataset_pack(),
            recipe=_training_recipe(),
            project_models_dir=tmp_path / "models",
            kohya_ss_dir=tmp_path / "kohya_ss",
            resume_checkpoint_path=checkpoint_dir,
        )
        assert "--save_state" in spec.args, (
            "--save_state must also be present when resuming, so the next checkpoint is saved"
        )


# ===========================================================================
# (R4) Path discovery: _discover_resume_checkpoint()
# ===========================================================================

class TestDiscoverResumeCheckpoint:
    def test_returns_none_when_output_dir_empty(self, tmp_path: Path) -> None:
        """If the output directory has no state dirs, return None."""
        output_dir = tmp_path / "out"
        output_dir.mkdir()
        result = _discover_resume_checkpoint(output_dir, output_name="mylora")
        assert result is None

    def test_returns_none_when_output_dir_missing(self, tmp_path: Path) -> None:
        """If the output directory does not exist, return None (not an error)."""
        missing = tmp_path / "nonexistent"
        result = _discover_resume_checkpoint(missing, output_name="mylora")
        assert result is None

    def test_picks_final_state_dir_over_numbered(self, tmp_path: Path) -> None:
        """The final state dir (<output_name>-state) is preferred over numbered ones."""
        output_dir = tmp_path / "out"
        output_dir.mkdir()
        # Numbered state dirs.
        (output_dir / "mylora-state000100").mkdir()
        (output_dir / "mylora-state000200").mkdir()
        # Final state dir (no number suffix).
        (output_dir / "mylora-state").mkdir()

        result = _discover_resume_checkpoint(output_dir, output_name="mylora")
        assert result is not None
        assert result == output_dir / "mylora-state", (
            f"Should prefer the final state dir; got: {result!r}"
        )

    def test_picks_highest_numbered_when_no_final(self, tmp_path: Path) -> None:
        """Without a final state dir, pick the highest numbered *-stateNNNNNN dir."""
        output_dir = tmp_path / "out"
        output_dir.mkdir()
        (output_dir / "mylora-state000100").mkdir()
        (output_dir / "mylora-state000300").mkdir()
        (output_dir / "mylora-state000200").mkdir()

        result = _discover_resume_checkpoint(output_dir, output_name="mylora")
        assert result is not None
        assert result == output_dir / "mylora-state000300", (
            f"Should pick the highest numbered state dir; got: {result!r}"
        )

    def test_ignores_dirs_with_different_output_name(self, tmp_path: Path) -> None:
        """State dirs from a different output_name must not be selected."""
        output_dir = tmp_path / "out"
        output_dir.mkdir()
        # These belong to a different run.
        (output_dir / "otherlora-state000100").mkdir()
        (output_dir / "otherlora-state").mkdir()

        result = _discover_resume_checkpoint(output_dir, output_name="mylora")
        assert result is None, (
            f"Must not pick state dirs from a different output_name; got: {result!r}"
        )

    def test_only_directories_are_considered(self, tmp_path: Path) -> None:
        """Files matching the *-state* pattern must be ignored; only dirs count."""
        output_dir = tmp_path / "out"
        output_dir.mkdir()
        # A file with a state-like name (not a dir).
        (output_dir / "mylora-state").write_text("not a dir")

        result = _discover_resume_checkpoint(output_dir, output_name="mylora")
        assert result is None, (
            "A file named like a state dir must not be returned (must be a directory)"
        )

    def test_returns_absolute_path(self, tmp_path: Path) -> None:
        """The returned path must be absolute."""
        output_dir = tmp_path / "out"
        output_dir.mkdir()
        (output_dir / "mylora-state").mkdir()

        result = _discover_resume_checkpoint(output_dir, output_name="mylora")
        assert result is not None
        assert result.is_absolute(), f"Path must be absolute; got: {result!r}"

    def test_single_numbered_dir_is_returned(self, tmp_path: Path) -> None:
        """A single numbered state dir is returned when it's the only one."""
        output_dir = tmp_path / "out"
        output_dir.mkdir()
        (output_dir / "run_lora-state000050").mkdir()

        result = _discover_resume_checkpoint(output_dir, output_name="run_lora")
        assert result == output_dir / "run_lora-state000050"


# ===========================================================================
# (R5) Executor integration: resume_checkpoint_path set after failure
# ===========================================================================

class TestExecutorSetsResumePathOnFailure:
    def test_resume_path_set_when_state_dirs_exist(self, tmp_path: Path) -> None:
        """After a failed job, executor must set resume_checkpoint_path to the
        discovered state dir if one exists in the output dir."""
        # Set up a fake output directory with a state dir.
        output_dir = tmp_path / "models"
        output_dir.mkdir()
        state_dir = output_dir / "kyuoka_lora-state"
        state_dir.mkdir()

        job = _make_job("job-resume-fail-001")

        # Build a failing FakeRunner that also "knows" the output_dir path via
        # the command args (the executor will derive it from the job).
        # We wire the executor so it uses enqueue_with_command carrying the real
        # output_dir in args (we just need the executor to know where to scan).
        # The executor's _discover_resume_checkpoint is called with the output_dir
        # derived from the job's dataset_path or from job.output_dir hint.
        # For this test we inject the output_dir directly via a custom subclass.

        stores: dict[str, list[TrainingJob]] = {_DEFAULT_PROJECT: [job]}

        def read_jobs(pid: str) -> list[TrainingJob]:
            return list(stores.setdefault(pid, []))

        def write_jobs(pid: str, new_jobs: list[TrainingJob]) -> None:
            stores[pid] = list(new_jobs)

        runner = FakeRunner(exit_code=1, stderr="Simulated training failure")
        sched = _make_scheduler()

        ex = TrainingExecutor(
            read_jobs=read_jobs,
            write_jobs=write_jobs,
            scheduler=sched,
            runner=runner,
        )

        # Use enqueue_with_command; supply args that encode the output_dir so the
        # executor can extract it.  The contract: when args contain
        # --output_dir=<path>, the executor scans that path for state dirs.
        args = [
            "python", "-m", "accelerate.commands.launch", "train_network.py",
            f"--output_dir={output_dir}",
            "--output_name=kyuoka_lora",
        ]
        ex.enqueue_with_command(_DEFAULT_PROJECT, "job-resume-fail-001", args, Path("."))

        failed = _wait(stores, "job-resume-fail-001", TrainingJobStatus.FAILED)
        assert failed.status == TrainingJobStatus.FAILED
        assert failed.resume_checkpoint_path is not None, (
            "resume_checkpoint_path must be set when a state dir exists in the output_dir"
        )
        assert str(state_dir) in failed.resume_checkpoint_path, (
            f"resume_checkpoint_path must point to the state dir; "
            f"got: {failed.resume_checkpoint_path!r}"
        )

    def test_resume_path_none_when_no_state_dirs(self, tmp_path: Path) -> None:
        """After a failed job with no state dirs, resume_checkpoint_path stays None."""
        output_dir = tmp_path / "models_empty"
        output_dir.mkdir()
        # No state dirs created — training failed before any checkpoint was saved.

        job = _make_job("job-resume-fail-002")
        stores: dict[str, list[TrainingJob]] = {_DEFAULT_PROJECT: [job]}

        def read_jobs(pid: str) -> list[TrainingJob]:
            return list(stores.setdefault(pid, []))

        def write_jobs(pid: str, new_jobs: list[TrainingJob]) -> None:
            stores[pid] = list(new_jobs)

        runner = FakeRunner(exit_code=1, stderr="Failure with no checkpoints")
        sched = _make_scheduler()

        ex = TrainingExecutor(
            read_jobs=read_jobs,
            write_jobs=write_jobs,
            scheduler=sched,
            runner=runner,
        )

        args = [
            "python", "-m", "accelerate.commands.launch", "train_network.py",
            f"--output_dir={output_dir}",
            "--output_name=kyuoka_lora",
        ]
        ex.enqueue_with_command(_DEFAULT_PROJECT, "job-resume-fail-002", args, Path("."))

        failed = _wait(stores, "job-resume-fail-002", TrainingJobStatus.FAILED)
        assert failed.status == TrainingJobStatus.FAILED
        assert failed.resume_checkpoint_path is None, (
            "resume_checkpoint_path must remain None when no state dirs were found"
        )

    def test_resume_path_not_set_on_success(self, tmp_path: Path) -> None:
        """On a successful job, resume_checkpoint_path must remain None."""
        output_dir = tmp_path / "models_success"
        output_dir.mkdir()
        # Even if state dirs exist, a completed job doesn't need resume_checkpoint_path.
        (output_dir / "kyuoka_lora-state").mkdir()

        job = _make_job("job-resume-success-001")
        stores: dict[str, list[TrainingJob]] = {_DEFAULT_PROJECT: [job]}

        def read_jobs(pid: str) -> list[TrainingJob]:
            return list(stores.setdefault(pid, []))

        def write_jobs(pid: str, new_jobs: list[TrainingJob]) -> None:
            stores[pid] = list(new_jobs)

        runner = FakeRunner(exit_code=0)
        sched = _make_scheduler()

        ex = TrainingExecutor(
            read_jobs=read_jobs,
            write_jobs=write_jobs,
            scheduler=sched,
            runner=runner,
        )

        args = [
            "python", "-m", "accelerate.commands.launch", "train_network.py",
            f"--output_dir={output_dir}",
            "--output_name=kyuoka_lora",
        ]
        ex.enqueue_with_command(_DEFAULT_PROJECT, "job-resume-success-001", args, Path("."))

        completed = _wait(stores, "job-resume-success-001", TrainingJobStatus.COMPLETED)
        assert completed.status == TrainingJobStatus.COMPLETED
        assert completed.resume_checkpoint_path is None, (
            "resume_checkpoint_path must be None on a successfully completed job"
        )
