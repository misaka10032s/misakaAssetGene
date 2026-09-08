"""Shared atomic JSON persistence for every ``core/*`` subsystem that keeps a
small on-disk state file (``jobs.json``, ``project.json``, worker runtime/
install state, ``origins.json``, ...) which is written by one request/thread
while another request/thread may be reading it concurrently (a background
progress poller, a status-polling API route, a second FastAPI worker thread).

Root cause this fixes (待回答 #53 item 2, ruling 2026-09-08): every writer used
to call ``path.write_text(...)`` directly. ``write_text`` truncates the file
in place, so a reader that opens the file between the truncate and the final
byte being flushed observes a partially-written (often empty or truncated)
JSON document and raises ``JSONDecodeError`` — this is exactly the race that
produced the intermittent "corrupt jobs.json read" reports (executor progress
writes fire under ``core.training.executor.TrainingExecutor._lock`` on a
background worker thread while ``TrainingService.stream_job_progress`` polls
the same file from the request thread with no coordination).

Two independent halves, BOTH required (the ruling explicitly asked for both,
not one or the other):

* Writer side — :func:`write_json_atomic` never truncates the real path.
  It serializes to a temp file in the same directory, flushes + fsyncs it,
  then ``os.replace``s it onto the destination. ``os.replace`` is a single
  atomic filesystem operation on NTFS (and POSIX): a concurrent reader that
  opens the destination path at any point sees either the complete old
  file or the complete new file, never a half-written one.
* Reader side — :func:`read_json_tolerant` assumes a writer *might still be
  mid-replace* (or a corrupt file was left by something outside this
  module, or a filesystem hiccup) and never lets a single bad read look
  like "the data vanished": it retries once (the replace window is
  microseconds, so a retry alone resolves nearly every race), then falls
  back to the last successfully-parsed content of that exact path cached
  in this process, and only raises if it has neither a fresh nor a cached
  reading to offer — matching the pre-fix behavior of an uncaught read
  bubbling up rather than a route silently rendering an empty list.

Explicitly NOT in scope here (owner ruling, verbatim): progress-reporting
throttling, SSE for generation, or any change to how often progress is
reported. This module only makes an existing read/write pair race-safe.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any

logger = logging.getLogger("misaka.atomic_io")

# os.replace() onto a path a reader has open can raise PermissionError on
# Windows. Python's own open() for reading requests
# FILE_SHARE_READ|WRITE|DELETE, so a replace is SUPPOSED to succeed even
# while a reader holds the destination open — but measured empirically
# (this module's own concurrency proof test, tests/test_atomic_jobs_io.py,
# 4 reader threads tight-looping with zero delay against one writer, using
# the REAL TrainingService._read_jobs/_write_jobs) a transient
# PermissionError still happens under sustained heavy contention, needing
# up to ~240 retries to clear when spaced with a growing (exponential,
# 1ms->20ms) backoff. Growing the delay was tried first and made things
# WORSE, not better: spacing attempts further apart sharply cuts how many
# retry attempts fit in a fixed wall-clock budget, so a 5s deadline at up to
# 20ms/attempt allows only ~240-400 attempts total — measured to still be
# insufficient (a write genuinely failed after exhausting a 5s exponential
# budget). A FLAT 1ms delay lets far more attempts fit in the same
# wall-clock window (a 5s budget then allows ~5000 attempts) — re-measured
# against the same worst-case contention shape, this consistently clears
# within a few hundred attempts (sub-second). Time-based (not a fixed
# attempt count) so the actual ceiling self-adjusts to whatever the
# per-attempt cost is on the machine running it; the deadline itself
# carries generous headroom because a real deployment's contention is far
# below this deliberately adversarial shape (one background writer thread
# vs. an SSE poll reading at ``poll_interval_sec=1.0``, not four zero-delay
# tight loops).
_REPLACE_RETRY_DEADLINE_SEC = 10.0
_REPLACE_RETRY_DELAY_SEC = 0.001

# The writer's tmp-file -> os.replace window is microseconds; one short
# sleep before the second read attempt is enough to clear it in practice.
_READ_RETRY_DELAY_SEC = 0.05

# One warning per corrupt-path per this many seconds, so a sustained failure
# doesn't spam the log once per poll tick.
_WARNING_THROTTLE_SEC = 60.0

# Both caches below are keyed by distinct file path and are otherwise never
# evicted on their own -- a long-running process that touches an
# ever-growing set of distinct paths (many short-lived project directories
# each with their own jobs.json/project.json, one runtime/install state file
# per worker, ...) would otherwise grow these dicts without bound (finding 3,
# 待回答 #53-2 review). Bounded to the _MAX_TRACKED_PATHS most-recently-used
# distinct paths via a plain OrderedDict LRU: a touched key moves to the
# "most recent" end, and the oldest entry is evicted once the cap is
# exceeded. 512 comfortably covers this app's real key space (per-project
# state files x realistic project count, plus a handful of per-worker
# runtime/install files) with headroom to spare.
_MAX_TRACKED_PATHS = 512

_snapshot_lock = threading.Lock()
_last_good_snapshots: OrderedDict[str, Any] = OrderedDict()
_last_warning_at: OrderedDict[str, float] = OrderedDict()


def _bounded_lru_set(store: OrderedDict[str, Any], key: str, value: Any) -> None:
    """Insert/update ``key`` in an LRU-bounded ``OrderedDict``, evicting the
    least-recently-touched entry once ``store`` would otherwise exceed
    ``_MAX_TRACKED_PATHS``. Caller must hold ``_snapshot_lock``.
    """
    store.pop(key, None)
    store[key] = value
    while len(store) > _MAX_TRACKED_PATHS:
        store.popitem(last=False)


def write_json_atomic(
    path: Path,
    obj: Any,
    *,
    ensure_ascii: bool = False,
    indent: int | None = 2,
    sort_keys: bool = False,
) -> None:
    """Serialize ``obj`` as JSON and write it to ``path`` atomically.

    Same on-disk text a plain ``path.write_text(json.dumps(...))`` would have
    produced (same ``ensure_ascii``/``indent``/``sort_keys`` knobs, trailing
    newline) — only the *mechanism* changes, so every existing reader keeps
    working unmodified. ``path``'s parent directory is created if missing.

    On any failure (encode error, disk full, a replace that exhausts its
    retries) the temp file is removed and the exception re-raised; ``path``
    itself is never touched until the replace succeeds, so a failed write
    leaves the previous good file in place.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(obj, ensure_ascii=ensure_ascii, indent=indent, sort_keys=sort_keys) + "\n"

    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        _replace_with_retry(tmp_path, path)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise


def _replace_with_retry(tmp_path: Path, dest_path: Path) -> None:
    deadline = time.monotonic() + _REPLACE_RETRY_DEADLINE_SEC
    while True:
        try:
            os.replace(tmp_path, dest_path)
            return
        except PermissionError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(_REPLACE_RETRY_DELAY_SEC)


#: Exceptions treated as a transient read-time race rather than real
#: corruption. Besides the obvious decode failures, Windows can also surface
#: a transient ``PermissionError``/``OSError`` (WinError 5, "Access is
#: denied") when a read lands in the same instant as a writer's
#: ``os.replace`` — measured directly via this module's own concurrency
#: proof test (tests/test_atomic_jobs_io.py) under 4 tight-looping reader
#: threads: without retrying on ``OSError`` here, that transient replace
#: collision propagated out of a *read* as an uncaught ``PermissionError``,
#: which is exactly the "reader crashes on a perfectly healthy file" failure
#: this whole module exists to prevent — just with a different exception
#: type than the JSONDecodeError the original defect produced. A brief
#: ``FileNotFoundError`` (the destination momentarily absent mid-replace) is
#: an ``OSError`` subclass and is covered by the same retry for the same
#: reason.
_TRANSIENT_READ_ERRORS = (json.JSONDecodeError, UnicodeDecodeError, OSError)

#: Sentinel distinguishing "no default was passed" from a caller explicitly
#: passing ``default=None`` (a legitimate value some call sites may want).
_NO_DEFAULT = object()


def read_json_tolerant(path: Path, *, default: Any = _NO_DEFAULT) -> Any:
    """Read and parse ``path`` as JSON, tolerating a writer mid-replace.

    Caller is expected to have already handled the "file does not exist yet"
    case (that is a normal empty-state condition, not corruption, and every
    call site here already branches on ``path.exists()`` before calling in).

    On a decode failure (including an empty file, which ``json.loads``
    reports as a decode error at position 0) or a transient OS-level race
    (see ``_TRANSIENT_READ_ERRORS``): retries once after a short sleep. If
    the retry also fails, returns the last successfully-parsed value for
    this exact path cached in this process (logging a throttled warning) —
    never silently substituting ``[]``/``{}``, which would look like the
    underlying data disappeared.

    If there is no cached snapshot either: when the caller passed
    ``default=`` (e.g. ``default={}``), that value is returned instead,
    also with a throttled warning logged (restores the pre-fix caller
    contract for readers such as ``core.integration.workers`` that used to
    catch ``JSONDecodeError`` themselves and fall back to ``{}`` — 待回答
    #53-2 review finding 2). When no ``default`` was passed, the second
    attempt's exception is re-raised, same as an unguarded
    ``json.loads(path.read_text())`` would have done before this helper
    existed — this is the contract ``core.training.service._read_jobs``
    relies on: it must never silently return an empty job list.
    """
    key = str(path)
    try:
        return _read_and_cache(path, key)
    except _TRANSIENT_READ_ERRORS:
        time.sleep(_READ_RETRY_DELAY_SEC)
        try:
            return _read_and_cache(path, key)
        except _TRANSIENT_READ_ERRORS as second_error:
            with _snapshot_lock:
                has_cached = key in _last_good_snapshots
                cached = _last_good_snapshots.get(key)
                if has_cached:
                    _last_good_snapshots.move_to_end(key)
            if has_cached:
                _warn_throttled(key, path, second_error)
                return cached
            if default is not _NO_DEFAULT:
                _warn_throttled(key, path, second_error)
                return default
            raise


def _read_and_cache(path: Path, key: str) -> Any:
    text = path.read_text(encoding="utf-8")
    value = json.loads(text)
    with _snapshot_lock:
        _bounded_lru_set(_last_good_snapshots, key, value)
    return value


def _warn_throttled(key: str, path: Path, error: Exception) -> None:
    now = time.monotonic()
    with _snapshot_lock:
        last = _last_warning_at.get(key, 0.0)
        if now - last < _WARNING_THROTTLE_SEC:
            return
        _bounded_lru_set(_last_warning_at, key, now)
    logger.warning(
        "read_json_tolerant: %s failed to parse twice (%s); serving last known-good snapshot",
        path,
        error,
    )
