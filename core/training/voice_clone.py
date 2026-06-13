"""GPT-SoVITS voice clone command construction (spec §7.2).

This module is a PURE FUNCTION layer — it builds the CLI argument list that the
executor will pass to a subprocess, but it does NOT launch any process itself.

Real-run deferred / wired-but-not-live-verified:
  The command vectors produced here are complete and correct by contract
  (tested with mocked subprocess in tests/test_executor.py), but they have
  NOT been verified against a live GPT-SoVITS installation or GPU.  The user
  must supply a real GPT-SoVITS clone and run the executor to verify end-to-end.
  See spec §7.3 and RESEARCH_LOG §10.

GPT-SoVITS modes (spec §7.2):
  * Zero-shot  — 3–10s reference audio, no training needed; just inference.
  * Fine-tune  — ≥ 1 hour reference corpus: slice → denoise → ASR → train.
    Produces .pth weights stored in <project>/models/voices/.

The GPT-SoVITS fine-tune pipeline (as of the ea2d2a81 commit in the manifest)
uses these entry-points inside the gpt-sovits clone:
  1. Slice/denoise:  tools/slice_audio.py  (optional, can be done offline)
  2. ASR:            tools/asr/funasr_asr.py
  3. Preprocess:     GPT_SoVITS/prepare_datasets/preprocess_ref.py
  4. S1 train:       GPT_SoVITS/s1_train.py
  5. S2 train:       GPT_SoVITS/s2_train.py

For this phase we build the S1 + S2 training commands, which are the two
long-running GPU steps.  Pre-processing (steps 1–3) is expected to have been
done beforehand; the DatasetPack.source path is assumed to contain the
prepared wavs and annotations.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

class VoiceCloneCommandSpec:
    """Resolved GPT-SoVITS invocation spec (pure data, no I/O).

    For fine-tune mode two command pairs are needed (S1 + S2 training stages);
    for zero-shot mode no training is needed — the spec carries only inference
    metadata so callers can log intent without launching any process.

    Attributes
    ----------
    mode            ``"zero_shot"`` or ``"fine_tune"``.
    s1_args         argv list for GPT/S1 training (``s1_train.py``).  None
                    when mode is ``"zero_shot"``.
    s2_args         argv list for SoVITS/S2 training (``s2_train.py``).  None
                    when mode is ``"zero_shot"``.
    cwd             Working directory — should be the gpt-sovits clone root.
    output_path     Where the trained ``.pth`` weights are expected to land.
    reference_audio Path to the reference audio file (for zero-shot) or the
                    corpus root (for fine-tune).
    """

    def __init__(
        self,
        *,
        mode: str,
        s1_args: list[str] | None,
        s2_args: list[str] | None,
        cwd: Path,
        output_path: Path,
        reference_audio: Path,
    ) -> None:
        self.mode = mode
        self.s1_args = s1_args
        self.s2_args = s2_args
        self.cwd = cwd
        self.output_path = output_path
        self.reference_audio = reference_audio

    @property
    def requires_training(self) -> bool:
        return self.mode == "fine_tune"

    def __repr__(self) -> str:
        return (
            f"VoiceCloneCommandSpec(mode={self.mode!r}, "
            f"s1_args={self.s1_args!r}, s2_args={self.s2_args!r}, "
            f"cwd={self.cwd!r}, output_path={self.output_path!r})"
        )


def build_voice_clone_command(
    *,
    character_name: str,
    reference_audio: Path,
    project_models_dir: Path,
    gpt_sovits_dir: Path,
    python_bin: str = "python",
    mode: str = "zero_shot",
    batch_size: int = 4,
    total_epoch: int = 8,
    save_every_epoch: int = 4,
) -> VoiceCloneCommandSpec:
    """Build GPT-SoVITS training (or zero-shot) invocation.

    Parameters
    ----------
    character_name
        Human-readable character name; used to derive output filenames.
    reference_audio
        For zero-shot: path to the reference .wav.
        For fine-tune: path to the prepared corpus root (wavs + list files).
    project_models_dir
        ``<project>/models/voices/`` — output weights land here.
    gpt_sovits_dir
        Absolute path to the gpt-sovits clone root (from workers/manifest.json).
    python_bin
        Python interpreter path inside the gpt-sovits venv.
    mode
        ``"zero_shot"`` (no training) or ``"fine_tune"`` (full S1+S2 train).
    batch_size
        Mini-batch size for both S1 and S2 training stages.
    total_epoch
        Total training epochs for GPT (S1) stage.
    save_every_epoch
        Save checkpoint every N epochs.

    Returns
    -------
    VoiceCloneCommandSpec
        Pure data object.  For zero-shot, ``s1_args`` and ``s2_args`` are None
        and no subprocess should be launched.

    Notes
    -----
    GPT-SoVITS entry-points used (spec §7.2 / manifest commit ea2d2a81):
      S1 GPT train:    GPT_SoVITS/s1_train.py
      S2 SoVITS train: GPT_SoVITS/s2_train.py

    The file list paths (``filelist_train``, ``filelist_val``) are derived
    from the corpus root by convention: ``<corpus_root>/train.list`` and
    ``<corpus_root>/val.list``.  These must exist before training is started.

    REAL-RUN DEFERRED: This command has NOT been verified against a live
    GPT-SoVITS installation.  See spec §7.3 and RESEARCH_LOG §10.
    """
    slug = _slugify(character_name)
    voices_dir = project_models_dir / "voices"
    voices_dir.mkdir(parents=True, exist_ok=True)
    output_path = voices_dir / f"{slug}_voice.pth"

    if mode == "zero_shot":
        return VoiceCloneCommandSpec(
            mode="zero_shot",
            s1_args=None,
            s2_args=None,
            cwd=gpt_sovits_dir,
            output_path=output_path,
            reference_audio=reference_audio,
        )

    # Fine-tune mode: build S1 (GPT) + S2 (SoVITS) train commands.
    corpus_root = reference_audio  # for fine-tune, reference_audio is the corpus root
    filelist_train = corpus_root / "train.list"
    filelist_val = corpus_root / "val.list"
    s1_output_dir = voices_dir / f"{slug}_s1"
    s2_output_dir = voices_dir / f"{slug}_s2"

    s1_args: list[str] = [
        python_bin,
        str(gpt_sovits_dir / "GPT_SoVITS" / "s1_train.py"),
        "--train_files", str(filelist_train),
        "--val_files", str(filelist_val),
        "--output_dir", str(s1_output_dir),
        "--batch_size", str(batch_size),
        "--total_epoch", str(total_epoch),
        "--save_every_n_epoch", str(save_every_epoch),
        "--exp_name", slug,
    ]

    s2_args: list[str] = [
        python_bin,
        str(gpt_sovits_dir / "GPT_SoVITS" / "s2_train.py"),
        "--train_files", str(filelist_train),
        "--val_files", str(filelist_val),
        "--output_dir", str(s2_output_dir),
        "--batch_size", str(batch_size),
        "--exp_name", slug,
    ]

    return VoiceCloneCommandSpec(
        mode="fine_tune",
        s1_args=s1_args,
        s2_args=s2_args,
        cwd=gpt_sovits_dir,
        output_path=output_path,
        reference_audio=reference_audio,
    )


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
# Legacy stub
# ---------------------------------------------------------------------------

def plan_training() -> dict[str, str]:
    """Legacy stub; replaced by build_voice_clone_command().  Do not use in new code."""
    return {"status": "use_build_voice_clone_command", "type": "voice_clone"}
