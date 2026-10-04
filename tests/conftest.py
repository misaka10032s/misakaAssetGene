"""Session-wide runtime write guard for the test suite (G3(c), pattern P9).

The static determinism check (``quality-gates/python/check_test_determinism.py``) cannot see a file
that product code writes while a test calls it. This session-wide guard is a ``sys.addaudithook`` hook:
the interpreter itself reports every file open for writing, every remove, rename, mkdir, rmdir,
truncate, symlink, link, copy, move and sqlite3 connect BEFORE it happens, whatever name the caller
bound the function to (``from os import remove`` before the fixture ran, ``io.open``, ``os.open``,
``io.FileIO``, ``tarfile.open``).

While the guard is active, a target inside the repo root and outside the system temp folder fails the
test with ``RepoWriteBlocked``, and is also recorded: product code that swallows the exception still
turns the run red when the session ends. An audit hook cannot be removed, so it is installed once and
does nothing while the flag is off. ``cv2.imwrite`` writes from C++ and raises no audit event, so it
alone is patched, when ``cv2`` is importable.

Allowed targets: anything under ``tempfile.gettempdir()`` (``tmp_path`` lives there), anything outside
the repo root, and the runner's own outputs: ``.pytest_cache``, ``__pycache__``, ``.coverage*``,
``coverage.xml``, ``htmlcov``.
"""

from __future__ import annotations

import functools
import importlib.util
import os
import sys
import tempfile
from collections.abc import Callable, Iterator
from typing import Any, ClassVar

import pytest

_REPO_ROOT = os.path.normcase(os.path.realpath(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
_RUNNER_OUTPUT_DIRS = (".pytest_cache", "__pycache__", "htmlcov")
_RUNNER_OUTPUT_FILES = (".coverage", "coverage.xml")
_WRITE_MODE_CHARS = frozenset("wax+")
_WRITE_OS_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_APPEND | os.O_TRUNC
# audit event -> the argument positions that name a target path (os.replace raises os.rename; os.unlink
# raises os.remove; os.makedirs and os.removedirs raise os.mkdir / os.remove / os.rmdir; shutil.copy and
# copy2 raise shutil.copyfile).
_AUDIT_TARGETS = {
    "os.remove": (0,),
    "os.rename": (0, 1),
    "os.mkdir": (0,),
    "os.rmdir": (0,),
    "os.truncate": (0,),
    "os.symlink": (1,),
    "os.link": (1,),
    "shutil.rmtree": (0,),
    "shutil.copyfile": (1,),
    "shutil.copytree": (1,),
    "shutil.move": (0, 1),
}


class RepoWriteBlocked(AssertionError):
    """A test, or code it called, tried to write under the repo root outside the system temp folder."""


class _WriteGuard:
    """The guard's session state, read by the audit hook."""

    active = False
    installed = False
    temp_root = ""
    violations: ClassVar[list[str]] = []


def _is_inside(parent: str, child: str) -> bool:
    try:
        return os.path.commonpath([parent, child]) == parent
    except ValueError:  # another drive
        return False


def _is_repo_write(target: object, temp_root: str) -> bool:
    """Whether ``target`` is inside the repo root, outside the temp folder and not a runner output."""
    if target is None or isinstance(target, int):  # nothing to resolve, or a descriptor opened elsewhere
        return False
    try:
        path = os.path.normcase(os.path.realpath(os.fsdecode(target)))
    except (TypeError, ValueError):
        return False
    if not _is_inside(_REPO_ROOT, path) or _is_inside(temp_root, path):
        return False
    relative = os.path.relpath(path, _REPO_ROOT).split(os.sep)
    if any(part in _RUNNER_OUTPUT_DIRS for part in relative):
        return False
    return not relative[-1].startswith(_RUNNER_OUTPUT_FILES)


def _sqlite_file(database: object) -> object:
    """The file a ``sqlite3.connect`` argument names, or None for an in-memory database."""
    text = os.fsdecode(database) if isinstance(database, str | bytes | os.PathLike) else ""
    if text.startswith("file:"):
        text = text[len("file:"):].split("?", 1)[0]
    return None if text in ("", ":memory:") else text


def _check(target: object, label: str) -> None:
    if _is_repo_write(target, _WriteGuard.temp_root):
        absolute = os.path.realpath(os.fsdecode(target))
        message = (
            f"noRepoWrites: {label}() would write {absolute}, "
            "which is inside the repo and outside the system temp folder"
        )
        _WriteGuard.violations.append(message)
        raise RepoWriteBlocked(message)


def _audit_hook(event: str, args: tuple[Any, ...]) -> None:
    if not _WriteGuard.active:
        return
    if event == "open":
        path, mode, flags = args
        # os.open passes mode None and the real flags; io.FileIO (behind open, io.open, tarfile.open)
        # passes its mode string with the flags it derived from it
        writing = (isinstance(flags, int) and flags & _WRITE_OS_FLAGS) or (
            mode is not None and _WRITE_MODE_CHARS & set(str(mode))
        )
        if writing:
            _check(path, "os.open" if mode is None else "open")
    elif event == "sqlite3.connect":
        _check(_sqlite_file(args[0]), "sqlite3.connect")
    elif event in _AUDIT_TARGETS:
        for position in _AUDIT_TARGETS[event]:
            if position < len(args):
                _check(args[position], event)


def _patch_cv2_imwrite() -> Callable[[], None]:
    """cv2.imwrite writes from C++ and raises no audit event. Returns the function that undoes the patch."""
    if importlib.util.find_spec("cv2") is None:
        return lambda: None
    import cv2

    original = cv2.imwrite

    @functools.wraps(original)
    def guarded(filename: Any, *args: Any, **kwargs: Any) -> Any:
        _check(filename, "cv2.imwrite")
        return original(filename, *args, **kwargs)

    cv2.imwrite = guarded

    def restore() -> None:
        cv2.imwrite = original

    return restore


@pytest.fixture(scope="session", autouse=True)
def _no_repo_writes() -> Iterator[None]:
    """Fail any test that writes inside the repo root outside the system temp folder."""
    _WriteGuard.violations.clear()
    _WriteGuard.temp_root = os.path.normcase(os.path.realpath(tempfile.gettempdir()))
    if not _WriteGuard.installed:
        sys.addaudithook(_audit_hook)
        _WriteGuard.installed = True
    restore_cv2 = _patch_cv2_imwrite()
    _WriteGuard.active = True
    try:
        yield
    finally:
        _WriteGuard.active = False
        restore_cv2()
    if _WriteGuard.violations:
        report = "\n".join(sorted(set(_WriteGuard.violations)))
        pytest.fail("tests wrote under the repo root:\n" + report, pytrace=False)
