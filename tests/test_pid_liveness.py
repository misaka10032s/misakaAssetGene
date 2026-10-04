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

These tests prove the replacement probe's DECISION LOGIC (a) tells the truth
about pid liveness on both branches and (b) never calls ``os.kill`` at all on
Windows, closing the exact hole that killed the worker.  The operating-system
interfaces (``os_name``, ``win_api``, ``kill``) are replaced by fakes, so no
real process, no real handle and no real signal is involved and the result is
the same on every operating system.
"""

from __future__ import annotations

import pytest

from core.integration.workers import _pid_alive

_STILL_ACTIVE = 259


class _FakeWindowsApi:
    """Fake of the three Windows process calls ``_pid_alive`` decides with."""

    def __init__(self, *, handle: int = 1234, code: int | None = _STILL_ACTIVE) -> None:
        self.handle = handle
        self.code = code
        self.opened: list[int] = []
        self.exit_code_queries: list[int] = []
        self.closed: list[int] = []

    def open_process(self, pid: int) -> int:
        self.opened.append(pid)
        return self.handle

    def exit_code(self, handle: int) -> int | None:
        self.exit_code_queries.append(handle)
        return self.code

    def close(self, handle: int) -> None:
        self.closed.append(handle)


def _forbidden_kill(pid: int, sig: int) -> None:
    raise AssertionError(f"_pid_alive must never call os.kill on Windows (pid={pid}, sig={sig})")


def test_pid_alive_true_when_windows_reports_still_active() -> None:
    api = _FakeWindowsApi(code=_STILL_ACTIVE)
    assert _pid_alive(4321, os_name="nt", win_api=api, kill=_forbidden_kill) is True
    assert api.opened == [4321]
    assert api.exit_code_queries == [1234]


@pytest.mark.parametrize("exit_code", [0, 1, 258, 260, 3221225786])
def test_pid_alive_false_when_windows_reports_an_exit_code(exit_code: int) -> None:
    api = _FakeWindowsApi(code=exit_code)
    assert _pid_alive(4321, os_name="nt", win_api=api, kill=_forbidden_kill) is False


def test_pid_alive_false_and_nothing_queried_when_open_process_fails() -> None:
    api = _FakeWindowsApi(handle=0)
    assert _pid_alive(4321, os_name="nt", win_api=api, kill=_forbidden_kill) is False
    assert api.exit_code_queries == []
    assert api.closed == [], "no handle was opened, so none may be closed"


def test_pid_alive_false_when_windows_exit_code_query_fails() -> None:
    api = _FakeWindowsApi(code=None)
    assert _pid_alive(4321, os_name="nt", win_api=api, kill=_forbidden_kill) is False
    assert api.closed == [1234], "the opened handle must be closed even when the query fails"


@pytest.mark.parametrize("code", [_STILL_ACTIVE, 0])
def test_pid_alive_closes_the_windows_handle_it_opened(code: int) -> None:
    api = _FakeWindowsApi(code=code)
    _pid_alive(4321, os_name="nt", win_api=api, kill=_forbidden_kill)
    assert api.closed == [1234]


@pytest.mark.parametrize("code", [_STILL_ACTIVE, 0])
def test_pid_alive_never_calls_os_kill_on_windows(code: int) -> None:
    """The regression that matters: the Windows branch of ``_pid_alive`` must
    use ``OpenProcess``/``GetExitCodeProcess`` exclusively and never fall
    through to ``os.kill`` -- which is exactly what sent CTRL_C to the whole
    console group and killed the live worker.  ``_forbidden_kill`` raises if the
    branch ever reaches it, for both an alive and an exited process."""
    api = _FakeWindowsApi(code=code)
    assert _pid_alive(4321, os_name="nt", win_api=api, kill=_forbidden_kill) is (code == _STILL_ACTIVE)


def test_pid_alive_posix_true_when_signal_zero_succeeds() -> None:
    calls: list[tuple[int, int]] = []

    def kill(pid: int, sig: int) -> None:
        calls.append((pid, sig))

    assert _pid_alive(4321, os_name="posix", kill=kill) is True
    assert calls == [(4321, 0)]


def test_pid_alive_posix_false_when_no_such_process() -> None:
    def kill(pid: int, sig: int) -> None:
        raise ProcessLookupError(pid)

    assert _pid_alive(4321, os_name="posix", kill=kill) is False


def test_pid_alive_posix_true_when_process_exists_but_cannot_be_signalled() -> None:
    def kill(pid: int, sig: int) -> None:
        raise PermissionError(pid)

    assert _pid_alive(4321, os_name="posix", kill=kill) is True
