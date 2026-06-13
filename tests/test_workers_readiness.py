"""Unit tests for live-first worker runtime readiness (spec §5.13).

The readiness_note must reflect whether a worker can *actually serve*: when a
ComfyUI health check responds, readiness is derived from the live server's
advertised checkpoints (object_info), not from the local ``workers/comfyui``
filesystem. The local clone / install / checkpoint checks only apply when the
worker is NOT running. HTTP is fully mocked here.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.generation.adapters import comfyui
from core.integration.workers import WorkersService


def _write_manifest(tmp_path: Path) -> Path:
    workers_root = tmp_path / "workers"
    workers_root.mkdir(parents=True)
    manifest = {
        "schema_version": 1,
        "workers": {
            "comfyui": {
                "display_name": "ComfyUI",
                "directory": "comfyui",
                "repo": "https://example.com/comfyui.git",
                "health_check": "http://127.0.0.1:8188/system_stats",
            }
        },
    }
    manifest_path = workers_root / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    return manifest_path


def _service(tmp_path: Path) -> WorkersService:
    return WorkersService(_write_manifest(tmp_path))


def _build_note(service: WorkersService, *, installed: bool, is_running: bool, has_local_ckpt: bool) -> str | None:
    clone_path = service.workers_root / "comfyui"
    if has_local_ckpt:
        ckpt_dir = clone_path / "models" / "checkpoints"
        ckpt_dir.mkdir(parents=True, exist_ok=True)
        (ckpt_dir / "model.safetensors").write_bytes(b"x")
    if installed:
        service._save_install_state("comfyui", {"status": "success", "installed_at": "now"})
    worker = service.get_worker_definition("comfyui")
    installed_reference = "abc123" if installed else None
    return service._build_readiness_note("comfyui", worker, clone_path, installed_reference, is_running, {})


def test_running_with_live_checkpoint_is_ready(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    service = _service(tmp_path)
    monkeypatch.setattr(comfyui, "fetch_live_checkpoints", lambda base_url, **k: ["sd_xl.safetensors"])
    # Running server advertises a checkpoint -> usable now, even with no local
    # clone and no local checkpoint files.
    note = _build_note(service, installed=False, is_running=True, has_local_ckpt=False)
    assert note is None


def test_running_without_live_checkpoint_is_blocked_live(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    service = _service(tmp_path)
    monkeypatch.setattr(comfyui, "fetch_live_checkpoints", lambda base_url, **k: [])
    note = _build_note(service, installed=True, is_running=True, has_local_ckpt=False)
    assert note == "No ComfyUI checkpoint is installed."


def test_not_running_but_installed_reports_not_running(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    service = _service(tmp_path)
    monkeypatch.setattr(comfyui, "fetch_live_checkpoints", lambda base_url, **k: [])
    # Installed locally with a local checkpoint, server down -> "not running".
    note = _build_note(service, installed=True, is_running=False, has_local_ckpt=True)
    assert note == "Worker server is not running."


def test_not_running_and_not_installed_reports_not_installed(tmp_path: Path) -> None:
    service = _service(tmp_path)
    note = _build_note(service, installed=False, is_running=False, has_local_ckpt=False)
    assert note == "Repository is not installed."


def test_running_ignores_missing_local_clone(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression for the live e2e defect: a standalone ComfyUI (no local clone,
    no local checkpoint dir) that serves checkpoints must NOT be reported as
    'Repository is not installed.'."""
    service = _service(tmp_path)
    monkeypatch.setattr(comfyui, "fetch_live_checkpoints", lambda base_url, **k: ["live.ckpt"])
    note = _build_note(service, installed=False, is_running=True, has_local_ckpt=False)
    assert note is None
