from __future__ import annotations

import os
import sys
from pathlib import Path

from core.generation.adapters.common import AdapterContext, AdapterExecutionResult, GeneratedArtifact
from core.models.schemas import Modality


def adapter_name() -> str:
    return "ultimate-rvc"


def execute(context: AdapterContext) -> AdapterExecutionResult:
    if context.source_asset_path is None:
        raise RuntimeError("ultimate-rvc requires a source audio asset.")

    worker_src = context.worker_path / "src"
    if not worker_src.exists():
        raise RuntimeError("ultimate-rvc source directory is missing.")

    if str(worker_src) not in sys.path:
        sys.path.insert(0, str(worker_src))

    os.environ.setdefault("URVC_MODELS_DIR", str((context.worker_path / "models").resolve()))
    os.environ.setdefault("URVC_AUDIO_DIR", str((context.worker_path / "audio").resolve()))
    os.environ.setdefault("URVC_TEMP_DIR", str((context.worker_path / "temp").resolve()))
    os.environ.setdefault("URVC_CONFIG_DIR", str((context.worker_path / "config").resolve()))

    try:
        from ultimate_rvc.core.generate.common import convert
        from ultimate_rvc.typing_extra import EmbedderModel, F0Method, RVCContentType
    except ImportError as exc:  # pragma: no cover - environment specific
        raise RuntimeError("ultimate-rvc dependencies are not available in the current Python environment.") from exc

    model_name = _resolve_voice_model_name(context.worker_path)
    output_dir = context.project_dir / ".cache" / "ultimate-rvc"
    output_dir.mkdir(parents=True, exist_ok=True)

    if context.report_progress:
        context.report_progress(20, "Preparing ultimate-rvc conversion")
    output_path = convert(
        audio_track=str(context.source_asset_path),
        directory=str(output_dir),
        model_name=model_name,
        n_octaves=0,
        n_semitones=0,
        f0_method=F0Method.RMVPE,
        index_rate=0.3,
        rms_mix_rate=1.0,
        protect_rate=0.33,
        split_audio=False,
        autotune_audio=False,
        autotune_strength=1.0,
        proposed_pitch=False,
        proposed_pitch_threshold=155.0,
        clean_audio=False,
        clean_strength=0.7,
        embedder_model=EmbedderModel.CONTENTVEC,
        custom_embedder_model=None,
        sid=0,
        content_type=RVCContentType.SPEECH,
        make_directory=True,
    )
    if context.report_progress:
        context.report_progress(85, "Saving converted voice")

    artifact = GeneratedArtifact(
        modality=Modality.VOICE,
        asset_type="voice_conversion",
        title=context.job.title,
        filename=f"{context.job.project_id}-{context.job.id[:8]}{output_path.suffix or '.wav'}",
        description=f"Converted with ultimate-rvc model {model_name}.",
        source_path=output_path,
    )
    return AdapterExecutionResult(artifacts=[artifact], metadata={"backend": "ultimate-rvc", "model_name": model_name})


def _resolve_voice_model_name(worker_path: Path) -> str:
    voice_models_dir = worker_path / "models" / "rvc" / "voice_models"
    candidates = sorted(path for path in voice_models_dir.iterdir() if path.is_dir()) if voice_models_dir.exists() else []
    for candidate in candidates:
        if any(file.suffix == ".pth" for file in candidate.iterdir() if file.is_file()):
            return candidate.name
    raise RuntimeError("ultimate-rvc has no installed voice model.")
