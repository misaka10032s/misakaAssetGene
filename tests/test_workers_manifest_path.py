"""Unit tests for WorkersService.resolve_installed_worker_path.

This is the manifest-reading seam TrainingExecutor now uses to resolve the
kohya_ss working directory (core/training/executor.py:_build_live_command),
replacing the old guess of ``Path(dataset_path).parent / "kohya_ss"``. See
tests/test_executor.py::TestKohyaWorkerDirResolution for the executor-level
integration coverage; this file covers WorkersService's own contract in
isolation (installed:false, missing path on disk, and the success case).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.integration.workers import WorkersService
from core.scheduler.vram import SchedulerError


def _write_manifest(tmp_path: Path, *, installed: bool | None) -> Path:
    workers_root = tmp_path / "workers"
    workers_root.mkdir(parents=True, exist_ok=True)
    worker_entry: dict = {
        "display_name": "kohya_ss",
        "directory": "kohya-ss",
        "repo": "https://github.com/bmaltais/kohya_ss.git",
    }
    if installed is not None:
        worker_entry["installed"] = installed
    manifest = {"schema_version": 1, "workers": {"kohya-ss": worker_entry}}
    manifest_path = workers_root / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    return manifest_path


def test_raises_clearly_when_manifest_marks_worker_not_installed(tmp_path: Path) -> None:
    service = WorkersService(_write_manifest(tmp_path, installed=False))
    with pytest.raises(SchedulerError, match="not installed"):
        service.resolve_installed_worker_path("kohya-ss")


def test_raises_clearly_when_directory_missing_on_disk(tmp_path: Path) -> None:
    # installed: true, but the directory was never actually cloned.
    service = WorkersService(_write_manifest(tmp_path, installed=True))
    with pytest.raises(SchedulerError, match="does not exist"):
        service.resolve_installed_worker_path("kohya-ss")


def test_returns_directory_field_path_when_installed_and_present(tmp_path: Path) -> None:
    manifest_path = _write_manifest(tmp_path, installed=True)
    service = WorkersService(manifest_path)
    clone_path = service.workers_root / "kohya-ss"
    clone_path.mkdir(parents=True)

    resolved = service.resolve_installed_worker_path("kohya-ss")

    assert resolved == clone_path


def test_unknown_worker_raises_key_error(tmp_path: Path) -> None:
    service = WorkersService(_write_manifest(tmp_path, installed=True))
    with pytest.raises(KeyError):
        service.resolve_installed_worker_path("nonexistent-worker")
