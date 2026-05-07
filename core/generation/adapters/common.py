from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from core.models.schemas import GenerationJob, Modality


@dataclass(slots=True)
class GeneratedArtifact:
    modality: Modality
    asset_type: str
    title: str
    filename: str
    description: str = ""
    content: bytes | None = None
    source_path: Path | None = None


@dataclass(slots=True)
class AdapterExecutionResult:
    artifacts: list[GeneratedArtifact] = field(default_factory=list)
    metadata: dict[str, str] = field(default_factory=dict)


@dataclass(slots=True)
class AdapterContext:
    project_dir: Path
    job: GenerationJob
    worker_path: Path
    health_check: str | None
    source_asset_path: Path | None = None
    mask_asset_path: Path | None = None
    report_progress: Callable[[int, str], None] | None = None
