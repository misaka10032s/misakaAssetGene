"""Session-wide runtime write guard for the test suite (G3(c), pattern P9).

The static determinism check (``quality-gates/python/check_test_determinism.py``) cannot see a file
that product code writes while a test calls it. This session fixture wraps the write functions below
for the whole run: when the target resolves inside the repo root and not under the system temp
folder, the call raises instead of writing, so the test that caused it fails.

Wrapped: ``open`` / ``io.open`` in a writing mode; ``pathlib.Path.write_text / write_bytes / mkdir /
touch / unlink / rename / replace / rmdir``; ``os.remove / unlink / rename / replace / mkdir /
makedirs / rmdir / removedirs / truncate``; ``shutil.rmtree / copy / copy2 / copyfile / copytree /
move``; ``sqlite3.connect`` (SQLite creates its file from C code, which no other wrapper can see).

Allowed targets: anything under ``tempfile.gettempdir()`` (``tmp_path`` lives there), anything outside
the repo root, and the runner's own outputs: ``.pytest_cache``, ``__pycache__``, ``.coverage*``,
``coverage.xml``, ``htmlcov``.
"""

from __future__ import annotations

import builtins
import io
import os
import pathlib
import shutil
import sqlite3
import tempfile
from collections.abc import Callable, Iterator
from typing import Any
from urllib.parse import unquote, urlparse

import pytest

_REPO_ROOT = os.path.realpath(os.path.join(os.path.dirname(__file__), os.pardir))
_WRITE_MODE_CHARS = "wax+"
_DIR_FD_KEYWORDS = ("dir_fd", "src_dir_fd", "dst_dir_fd")


def _is_inside(parent: str, child: str) -> bool:
    """Whether ``child`` is ``parent`` itself or lies under it (both absolute, resolved)."""
    parent_norm = os.path.normcase(parent)
    child_norm = os.path.normcase(child)
    try:
        return os.path.commonpath([parent_norm, child_norm]) == parent_norm
    except ValueError:  # another drive
        return False


def _is_runner_output(relative: str) -> bool:
    """Whether a repo-relative path is a file the test runner itself writes."""
    parts = relative.replace("\\", "/").split("/")
    first = parts[0]
    return (
        "__pycache__" in parts
        or first in (".pytest_cache", "htmlcov", "coverage.xml")
        or first.startswith(".coverage")
    )


def _make_check(temp_root: str) -> Callable[[object, str], None]:
    def check(target: object, function: str) -> None:
        if isinstance(target, bool) or not isinstance(target, str | bytes | os.PathLike):
            return  # a file descriptor, None or anything that is not a path
        absolute = os.path.realpath(os.fsdecode(target))
        if _is_inside(temp_root, absolute) or not _is_inside(_REPO_ROOT, absolute):
            return
        if _is_runner_output(os.path.relpath(absolute, _REPO_ROOT)):
            return
        raise RuntimeError(
            f"noRepoWrites: {function}() would write {absolute}, "
            "which is inside the repo and outside the system temp folder"
        )

    return check


def _uses_dir_fd(kwargs: dict[str, Any]) -> bool:
    return any(kwargs.get(name) is not None for name in _DIR_FD_KEYWORDS)


def _guarded_function(
    original: Callable[..., Any], name: str, indexes: tuple[int, ...], check: Callable[[object, str], None]
) -> Callable[..., Any]:
    def guarded(*args: Any, **kwargs: Any) -> Any:
        if not _uses_dir_fd(kwargs):  # a path relative to a directory handle was checked at its root call
            for index in indexes:
                if index < len(args):
                    check(args[index], name)
        return original(*args, **kwargs)

    return guarded


def _guarded_open(original: Callable[..., Any], name: str, check: Callable[[object, str], None]) -> Callable[..., Any]:
    def guarded(file: Any, mode: Any = "r", *args: Any, **kwargs: Any) -> Any:
        if isinstance(mode, str) and any(char in mode for char in _WRITE_MODE_CHARS):
            check(file, name)
        return original(file, mode, *args, **kwargs)

    return guarded


def _sqlite_target(database: object) -> object:
    """The file path a ``sqlite3.connect`` argument names, or None for an in-memory database."""
    if isinstance(database, bytes):
        database = os.fsdecode(database)
    if isinstance(database, os.PathLike):
        database = os.fspath(database)
    if not isinstance(database, str) or database in ("", ":memory:"):
        return None
    if database.startswith("file:"):
        parsed = urlparse(database)
        if "mode=memory" in parsed.query:
            return None
        path = unquote(parsed.path)
        if len(path) > 2 and path[0] == "/" and path[2] == ":":  # file:///C:/x on Windows
            path = path[1:]
        return path or None
    return database


def _guarded_sqlite_connect(original: Callable[..., Any], check: Callable[[object, str], None]) -> Callable[..., Any]:
    def guarded(database: Any, *args: Any, **kwargs: Any) -> Any:
        check(_sqlite_target(database), "sqlite3.connect")
        return original(database, *args, **kwargs)

    return guarded


@pytest.fixture(scope="session", autouse=True)
def _no_repo_writes() -> Iterator[None]:
    """Fail any test that writes inside the repo root outside the system temp folder."""
    check = _make_check(os.path.realpath(tempfile.gettempdir()))
    with pytest.MonkeyPatch.context() as patch:
        for module, names in (
            (os, {"remove": (0,), "unlink": (0,), "rename": (0, 1), "replace": (0, 1), "mkdir": (0,), "makedirs": (0,),
                  "rmdir": (0,), "removedirs": (0,), "truncate": (0,)}),
            (shutil, {"rmtree": (0,), "copy": (1,), "copy2": (1,), "copyfile": (1,), "copytree": (1,), "move": (0, 1)}),
        ):
            for name, indexes in names.items():
                label = f"{module.__name__}.{name}"
                patch.setattr(module, name, _guarded_function(getattr(module, name), label, indexes, check))
        for name, indexes in {
            "write_text": (0,), "write_bytes": (0,), "mkdir": (0,), "touch": (0,), "unlink": (0,),
            "rename": (0, 1), "replace": (0, 1), "rmdir": (0,),
        }.items():
            guarded = _guarded_function(getattr(pathlib.Path, name), f"Path.{name}", indexes, check)
            patch.setattr(pathlib.Path, name, guarded)
        patch.setattr(builtins, "open", _guarded_open(builtins.open, "open", check))
        patch.setattr(io, "open", _guarded_open(io.open, "io.open", check))
        patch.setattr(sqlite3, "connect", _guarded_sqlite_connect(sqlite3.connect, check))
        yield
