"""LoRA training command construction for kohya_ss (spec §7.1).

This module is a PURE FUNCTION layer — it builds the CLI argument list and
working-directory path that the executor will pass to a subprocess.  It does
NOT launch any process itself and does NOT perform any filesystem I/O.
Directory creation (``output_dir.mkdir(...)``) is intentionally deferred to
the executor so this layer remains side-effect-free and unit-testable without
a real filesystem.

Real-run deferred / wired-but-not-live-verified:
  The command vectors produced here are complete and correct by contract
  (tested with mocked subprocess in tests/test_executor.py), but they have
  NOT been verified against a live kohya_ss installation or GPU.  The user
  must supply a real kohya_ss clone and run the executor to verify end-to-end.
  See spec §7.3 and RESEARCH_LOG §10.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from core.models.schemas import CharacterSheet, DatasetPack, TrainingRecipe


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

# kohya_ss v25.0.3+ moved train_network.py (and the other training entry
# points) out of the clone root and into the `sd-scripts/` git submodule
# (https://github.com/kohya-ss/sd-scripts, declared in kohya_ss's own
# .gitmodules).  Every script path built from `kohya_ss_dir` must go through
# this constant rather than concatenating the subdir inline, so a future
# layout change only needs one edit.  Verified against the real install at
# workers/kohya-ss (workers/manifest.json, tag v25.0.3, 2026-09-07).
KOHYA_SCRIPTS_SUBDIR = "sd-scripts"


class LoraCommandSpec:
    """Fully-resolved kohya_ss CLI invocation (pure data, no I/O).

    Attributes
    ----------
    args        Full argv list suitable for ``subprocess.run(args, ...)``.
                First element is always the Python interpreter path (or
                "python" when the venv root is not known).
    cwd         Working directory for the subprocess — should be the
                kohya_ss clone root so relative config paths resolve.
    output_path Absolute path where kohya_ss will write the output
                ``.safetensors`` file.
    """

    def __init__(self, args: list[str], cwd: Path, output_path: Path) -> None:
        self.args = args
        self.cwd = cwd
        self.output_path = output_path

    def __repr__(self) -> str:
        return (
            f"LoraCommandSpec(args={self.args!r}, cwd={self.cwd!r}, "
            f"output_path={self.output_path!r})"
        )


def build_lora_command(
    *,
    character_sheet: CharacterSheet,
    dataset_pack: DatasetPack,
    recipe: TrainingRecipe,
    project_models_dir: Path,
    kohya_ss_dir: Path,
    python_bin: str = "python",
    resume_checkpoint_path: Path | None = None,
    save_every_n_epochs: int = 1,
) -> LoraCommandSpec:
    """Build the kohya_ss accelerate-launch invocation for LoRA training.

    Parameters
    ----------
    character_sheet
        CharacterSheet entity — supplies trigger words and the character name
        used to derive the output filename.
    dataset_pack
        DatasetPack entity — supplies the training data directory path.
    recipe
        TrainingRecipe entity — supplies base_model, rank, epochs, optimizer
        and caption_strategy.
    project_models_dir
        Absolute path to ``<project>/models/``.  The output ``.safetensors``
        is written to ``<project_models_dir>/<slug>_lora.safetensors``.
    kohya_ss_dir
        Absolute path to the kohya_ss clone root.
    python_bin
        Path to the Python interpreter inside the kohya_ss venv.  Defaults
        to ``"python"`` for testability.
    resume_checkpoint_path
        When provided, ``--resume <path>`` is appended to the argv so kohya_ss
        restores optimizer / scheduler / step state from the saved-state
        directory.  The value must be an absolute path to a ``*-state`` or
        ``*-stateNNNNNN`` directory produced by a previous run that used
        ``--save_state``.  Leave as ``None`` for a fresh (non-resume) submit.
        Source: kohya-ss/sd-scripts issue #789, bmaltais/kohya_ss issue #2384.
    save_every_n_epochs
        Cadence for ``--save_every_n_epochs``.  Must be >= 1.  Defaults to 1
        (save a state dir after every epoch) so that at least one resume point
        exists if training fails.  ``--save_state`` is always paired with this
        argument — omitting the cadence would produce no checkpoint dirs.

    Returns
    -------
    LoraCommandSpec
        Pure data object carrying args + cwd + output_path.  The executor
        reads these fields and passes them verbatim to the subprocess runner.

    Notes
    -----
    Spec §7.1 mandates kohya_ss CLI via ``accelerate launch train_network.py``.
    The ``--network_module`` is ``networks.lora`` (standard kohya_ss module).
    Caption strategy ``wd14`` uses ``--caption_extension .txt`` (pre-generated
    captions); ``blip`` and ``manual`` use the same convention (captions must
    already exist in the dataset directory).

    REAL-RUN DEFERRED: This command has NOT been verified against a live
    kohya_ss installation.  See spec §7.3 and RESEARCH_LOG §10.
    """
    trigger_word = character_sheet.trigger_words[0] if character_sheet.trigger_words else character_sheet.name
    slug = _slugify(character_sheet.name)
    output_name = f"{slug}_lora"
    output_dir = project_models_dir
    # Directory creation is the executor's responsibility at run time.
    # This function is a pure command builder; it must not perform I/O.
    output_path = output_dir / f"{output_name}.safetensors"

    # Caption extension: WD14 and BLIP both write .txt sidecars alongside images.
    caption_extension = ".txt"

    args: list[str] = [
        python_bin,
        "-m", "accelerate.commands.launch",
        str(kohya_ss_dir / KOHYA_SCRIPTS_SUBDIR / "train_network.py"),
        # Model
        f"--pretrained_model_name_or_path={recipe.base_model}",
        # Dataset
        f"--train_data_dir={dataset_pack.source}",
        f"--caption_extension={caption_extension}",
        # Output
        f"--output_dir={output_dir}",
        f"--output_name={output_name}",
        # Network (LoRA)
        "--network_module=networks.lora",
        f"--network_dim={recipe.rank}",
        f"--network_alpha={recipe.rank // 2}",
        # Training
        f"--max_train_epochs={recipe.epochs}",
        f"--optimizer_type={recipe.optimizer}",
        # Precision (safe default; can be overridden via recipe.params in future)
        "--mixed_precision=fp16",
        "--save_precision=fp16",
        "--save_model_as=safetensors",
        # Checkpoint saving for resume support (spec §7.3).
        # --save_state saves an Accelerate training-state dir at the same cadence
        # as model saves.  --save_every_n_epochs sets that cadence (must be paired).
        # Sources: kohya-ss/sd-scripts #789, bmaltais/kohya_ss #2384 / #772.
        "--save_state",
        f"--save_every_n_epochs={save_every_n_epochs}",
        # Logging
        "--logging_dir=logs",
        "--log_prefix=misaka_lora",
    ]

    # Resume from a previously saved state directory (spec §7.3).
    # --resume <DIR> restores optimizer/scheduler/step state.  Value is the path
    # to the saved-state directory (NOT a model file).  Only appended when a
    # checkpoint path is explicitly supplied — never on a fresh submit.
    if resume_checkpoint_path is not None:
        args += ["--resume", str(resume_checkpoint_path)]

    return LoraCommandSpec(args=args, cwd=kohya_ss_dir, output_path=output_path)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _slugify(text: str) -> str:
    """Convert a name to a filesystem-safe slug."""
    import re
    slug = text.strip().lower()
    slug = re.sub(r"[^\w一-鿿぀-ヿ]+", "_", slug)
    slug = slug.strip("_")
    return slug or "character"


# ---------------------------------------------------------------------------
# Legacy stub (kept for backward-compatibility; delegates to build_lora_command
# when called without arguments from old call sites)
# ---------------------------------------------------------------------------

def plan_training() -> dict[str, str]:
    """Legacy stub; replaced by build_lora_command().  Do not use in new code."""
    return {"status": "use_build_lora_command", "type": "lora"}
