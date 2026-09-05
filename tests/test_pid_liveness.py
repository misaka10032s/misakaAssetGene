"""Unit tests for the cross-platform, side-effect-free pid liveness probe.

Regression context (measured 2026-09-05,
``D:/backup/CSIA/@PM/state/runs/misakaAssetGene-gen-test-260904/D-report.md`` § E):
``core/integration/workers.py``'s old ``_resolve_managed_pid`` used
``os.kill(pid, 0)`` as an "is the process alive" probe. On Windows, ``sig=0``
is numerically identical to ``signal.CTRL_C_EVENT`` (0), so CPython routes it
through ``GenerateConsoleCtrlEvent`` -- a *broadcast* to the whole console
process group, not a targeted, side-effect-free probe. This raised a
``SystemError`` inside a request handler AND, in the same instant, silently
killed a live ACE-Step worker sharing the app's console (14 GB VRAM loaded).

These tests prove the replacement probe (a) still tells the truth about pid
liveness on every platform, and (b) never calls ``os.kill`` at all on
Windows, closing the exact hole that killed the worker.
"""

from __future__ import annotations

import os
import subprocess
import sys

import pytest

from core.integration.workers import _pid_alive


def test_pid_alive_true_for_own_pid() -> None:
    assert _pid_alive(os.getpid()) is True


def test_pid_alive_false_for_exited_pid() -> None:
    process = subprocess.Popen([sys.executable, "-c", "pass"])
    pid = process.pid
    process.wait(timeout=10)
    assert _pid_alive(pid) is False


@pytest.mark.skipif(os.name != "nt", reason="os.kill(pid, 0) misuse is Windows-specific; nothing to regress on POSIX")
def test_pid_alive_never_calls_os_kill_on_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    """The regression that matters: the Windows branch of ``_pid_alive`` must
    use ``OpenProcess``/``GetExitCodeProcess`` exclusively and never fall
    through to ``os.kill`` -- which is exactly what sent CTRL_C to the whole
    console group and killed the live worker."""

    def _forbidden(pid: int, sig: int) -> None:
        raise AssertionError(f"_pid_alive must never call os.kill on Windows (pid={pid}, sig={sig})")

    monkeypatch.setattr(os, "kill", _forbidden)

    assert _pid_alive(os.getpid()) is True

    process = subprocess.Popen([sys.executable, "-c", "pass"])
    pid = process.pid
    process.wait(timeout=10)
    assert _pid_alive(pid) is False
