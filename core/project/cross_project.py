"""Cross-project reference resolver with RW lock (spec §5.6).

The RW lock protects the _external/ copy step and origin updates from
concurrent write conflicts (RESEARCH_LOG §3.4 conclusion).

Implementation uses a per-path lock-file strategy that is Windows-compatible
without requiring any third-party library:
  - msvcrt.locking on Windows (exclusive byte-range lock on a .lock file)
  - fcntl.flock on POSIX

The public helpers copy_external_asset() and update_origins_json() acquire an
exclusive lock around their write operations.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Generator


REFERENCE_PATTERN = re.compile(
    r"^@(?P<project>[a-zA-Z0-9_-]+)/(?P<asset_path>[a-zA-Z0-9_./-]+)(?:#(?P<version>[a-zA-Z0-9_-]+))?$"
)


def parse_reference(reference: str) -> dict[str, str] | None:
    """Parse an @project/path#version reference string.

    Returns a dict with keys 'project', 'asset_path', 'version', or None if the
    reference does not match the expected syntax.
    """
    match = REFERENCE_PATTERN.match(reference)
    if not match:
        return None
    return {key: value or "" for key, value in match.groupdict().items()}


# ---------------------------------------------------------------------------
# Lock-file based RW lock (Windows + POSIX compatible, no extra dependencies)
# ---------------------------------------------------------------------------

_lock_registry: dict[str, threading.Lock] = {}
_lock_registry_mu = threading.Lock()


def _get_thread_lock(lock_path: Path) -> threading.Lock:
    """Return a per-path threading.Lock (in-process serialisation layer)."""
    key = str(lock_path.resolve())
    with _lock_registry_mu:
        if key not in _lock_registry:
            _lock_registry[key] = threading.Lock()
        return _lock_registry[key]


@contextmanager
def _acquire_file_lock(lock_path: Path, timeout: float = 10.0) -> Generator[None, None, None]:
    """Acquire an exclusive OS-level lock on lock_path for the duration of the context.

    Uses msvcrt on Windows and fcntl on POSIX.  Falls back to a busy-wait with
    a timeout if the OS-level lock is unavailable (should not happen under
    normal conditions).

    Also acquires the in-process threading.Lock so that concurrent threads in
    the same process are serialised even before touching the file.
    """
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    thread_lock = _get_thread_lock(lock_path)

    deadline = time.monotonic() + timeout
    acquired_thread = thread_lock.acquire(timeout=timeout)
    if not acquired_thread:
        raise TimeoutError(f"Could not acquire thread lock for {lock_path} within {timeout}s")

    try:
        fh = open(lock_path, "w")  # noqa: WPS515 (open in context)
        try:
            if sys.platform == "win32":
                import msvcrt
                _deadline = deadline
                while True:
                    try:
                        msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
                        break
                    except OSError:
                        if time.monotonic() > _deadline:
                            raise TimeoutError(
                                f"Could not acquire file lock for {lock_path} within {timeout}s"
                            )
                        time.sleep(0.05)
                try:
                    yield
                finally:
                    try:
                        msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
                    except OSError:
                        pass
            else:
                import fcntl
                deadline2 = deadline
                while True:
                    try:
                        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        break
                    except BlockingIOError:
                        if time.monotonic() > deadline2:
                            raise TimeoutError(
                                f"Could not acquire file lock for {lock_path} within {timeout}s"
                            )
                        time.sleep(0.05)
                try:
                    yield
                finally:
                    fcntl.flock(fh, fcntl.LOCK_UN)
        finally:
            fh.close()
            try:
                lock_path.unlink(missing_ok=True)
            except OSError:
                pass
    finally:
        thread_lock.release()


def _external_lock_path(project_dir: Path) -> Path:
    """Return the lock file path for a project's _external/ directory."""
    return project_dir / "_external" / ".import.lock"


# ---------------------------------------------------------------------------
# Public cross-project helpers
# ---------------------------------------------------------------------------

def copy_external_asset(
    source_path: Path,
    dest_project_dir: Path,
    source_project_id: str,
    relative_asset_path: str,
    *,
    lock_timeout: float = 10.0,
) -> Path:
    """Copy source_path into dest_project_dir/_external/<source_project_id>/... under an exclusive lock.

    Returns the destination path.

    The lock prevents two concurrent processes/threads from writing the same
    _external/ entry simultaneously (RESEARCH_LOG §3.4).
    """
    dest_external = dest_project_dir / "_external" / source_project_id
    dest_file = dest_external / relative_asset_path

    lock_path = _external_lock_path(dest_project_dir)
    with _acquire_file_lock(lock_path, timeout=lock_timeout):
        dest_file.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_path, dest_file)

    return dest_file


def update_origins_json(
    dest_project_dir: Path,
    source_project_id: str,
    relative_asset_path: str,
    origin_metadata: dict,
    *,
    lock_timeout: float = 10.0,
) -> None:
    """Update _external/origins.json with a new or updated origin entry under an exclusive lock.

    origins.json schema: {"<source_project_id>/<relative_asset_path>": {<metadata>}}
    """
    origins_path = dest_project_dir / "_external" / "origins.json"
    lock_path = _external_lock_path(dest_project_dir)
    key = f"{source_project_id}/{relative_asset_path}"

    with _acquire_file_lock(lock_path, timeout=lock_timeout):
        if origins_path.exists():
            try:
                origins = json.loads(origins_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                origins = {}
        else:
            origins = {}

        origins[key] = {**origin_metadata, "source_project_id": source_project_id}
        origins_path.parent.mkdir(parents=True, exist_ok=True)
        origins_path.write_text(
            json.dumps(origins, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
