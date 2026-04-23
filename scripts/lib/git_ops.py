from __future__ import annotations

from pathlib import Path


def worker_clone_path(workers_root: Path, worker_name: str) -> Path:
    return workers_root / worker_name
