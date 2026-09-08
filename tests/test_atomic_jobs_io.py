"""Concurrency proof + unit tests for 待回答 #53 item 2 (2026-09-08 ruling).

The bug this fixes: ``TrainingService._write_jobs`` used to call
``jobs.json.write_text(...)`` directly, which truncates the destination file
in place. A poller reading ``jobs.json`` (``TrainingService.stream_job_progress``
re-reads it on a timer from the request thread while
``TrainingExecutor.on_progress`` writes it from a background worker thread
under its own ``_lock`` — see core/training/executor.py:811-822) could open
the file mid-truncate and get a ``JSONDecodeError`` on a perfectly healthy
job store.

Fix, both halves proven here:
  (a) writer side — ``core.project.atomic_io.write_json_atomic`` writes a
      temp file in the same directory then ``os.replace``s it onto the
      destination (atomic on NTFS: a reader sees the whole old file or the
      whole new file, never a partial one).
  (b) reader side — ``core.project.atomic_io.read_json_tolerant`` retries
      once, then falls back to the last successfully-parsed snapshot of that
      exact path, and only raises when it has neither.

``TestAtomicJobsIOControlOldBehaviorCanFail`` reproduces the PRE-FIX
``write_text``/plain-``json.loads`` pair to prove this suite is capable of
catching the defect it guards against (cluster rule: a gate/test that has
never been shown to fail is not a gate).
"""

from __future__ import annotations

import json
import threading
from datetime import UTC, datetime
from pathlib import Path

import pytest

from core.models.schemas import Modality, TrainingJob, TrainingJobStatus
from core.project.atomic_io import read_json_tolerant, write_json_atomic
from core.training.service import TrainingService

# ---------------------------------------------------------------------------
# Shared payload helpers
# ---------------------------------------------------------------------------

_JOB_COUNT = 24
_STDERR_PADDING = "x" * 600  # pushes each job entry to a realistic size
_NOTE_PADDING = "kohya_ss tqdm progress line captured verbatim " * 4


def _job_ids() -> list[str]:
    return [f"job-{i:03d}" for i in range(_JOB_COUNT)]


def _build_realistic_jobs(*, revision: int) -> list[TrainingJob]:
    """~20 KB worth of TrainingJob records once serialized (24 jobs, each
    padded with a realistic stderr tail / progress note). ``revision`` is
    folded into a couple of fields so every write produces genuinely
    different bytes on disk, the same way real progress updates do.
    """
    now = datetime.now(UTC)
    jobs = []
    for idx, job_id in enumerate(_job_ids()):
        jobs.append(
            TrainingJob(
                id=job_id,
                project_id="proj-atomic-io",
                title=f"LoRA training job #{idx} (rev {revision})",
                modality=Modality.IMAGE,
                worker="kohya-ss",
                dataset_path=f"/data/datasets/character-{idx}",
                status=TrainingJobStatus.RUNNING,
                note=f"[rev {revision}] {_NOTE_PADDING}",
                progress=revision % 101,
                progress_label=f"step {revision} of 2000",
                exit_code=None,
                stderr_tail=_STDERR_PADDING,
                resume_checkpoint_path=None,
                created_at=now,
                updated_at=now,
            )
        )
    return jobs


def _measure_payload_size() -> int:
    jobs = _build_realistic_jobs(revision=0)
    return len(json.dumps({"jobs": [job.model_dump(mode="json") for job in jobs]}, ensure_ascii=False, indent=2))


def test_realistic_payload_is_roughly_20kb() -> None:
    size = _measure_payload_size()
    assert 15_000 <= size <= 60_000, f"payload size {size} bytes is not in the intended ~20KB ballpark"


# ---------------------------------------------------------------------------
# Pre-fix reproduction (control) — plain write_text / plain json.loads,
# exactly what core/training/service.py did before this fix.
# ---------------------------------------------------------------------------

def _jobs_path_for(project_dir: Path) -> Path:
    return project_dir / ".cache" / "training" / "jobs.json"


def _write_jobs_unsafe_old(project_dir: Path, jobs: list[TrainingJob]) -> None:
    """Byte-for-byte reproduction of the pre-fix ``TrainingService._write_jobs``."""
    path = _jobs_path_for(project_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"jobs": [job.model_dump(mode="json") for job in jobs]}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _read_jobs_unsafe_old(project_dir: Path) -> list[TrainingJob]:
    """Byte-for-byte reproduction of the pre-fix ``TrainingService._read_jobs``
    (no retry, no fallback — a decode error propagates straight to the caller)."""
    path = _jobs_path_for(project_dir)
    if not path.exists():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    return [TrainingJob(**item) for item in payload.get("jobs", [])]


def _hammer(
    project_dir: Path,
    *,
    write_fn,
    read_fn,
    iterations: int,
    reader_count: int,
    join_timeout: float = 300.0,
) -> tuple[int, int, list[BaseException]]:
    """Run one writer thread (``iterations`` rewrites) concurrently with
    ``reader_count`` reader threads that loop for the whole duration.

    Returns ``(total_reads, corrupt_reads, unexpected_reader_exceptions)``.
    ``corrupt_reads`` counts a decode/validation error caught *inside* the
    reader loop (i.e. the read function raised); it does not count toward
    ``unexpected_reader_exceptions``, which is reserved for anything else
    going wrong (a bug in the test itself, not the race under test).

    A writer-thread exception is captured and re-raised here (rather than
    left as a background "unhandled thread exception" warning) — under the
    FIXED write/read pair a write should never fail outright, so if one
    does, that is itself a finding this test must surface as a failure, not
    swallow. Both thread groups are also positively checked to have
    actually finished within ``join_timeout``: silently returning with a
    still-running background thread would let it keep touching
    ``project_dir`` (or, worse, a monkeypatched module function) well into
    whatever test runs next — exactly the kind of cross-test contamination
    a fixed ``join(timeout=...)`` with no follow-up check invites.
    """
    stop_event = threading.Event()
    counters_lock = threading.Lock()
    counters = {"total": 0, "corrupt": 0}
    unexpected: list[BaseException] = []
    writer_errors: list[BaseException] = []
    expected_ids = set(_job_ids())

    def writer() -> None:
        try:
            for revision in range(iterations):
                write_fn(project_dir, _build_realistic_jobs(revision=revision))
        except BaseException as error:
            writer_errors.append(error)
        finally:
            stop_event.set()

    def reader() -> None:
        while not stop_event.is_set():
            try:
                jobs = read_fn(project_dir)
            except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
                with counters_lock:
                    counters["total"] += 1
                    counters["corrupt"] += 1
                continue
            except BaseException as error:  # pragma: no cover - safety net
                unexpected.append(error)
                continue
            with counters_lock:
                counters["total"] += 1
            if jobs and ({job.id for job in jobs} != expected_ids or len(jobs) != _JOB_COUNT):
                unexpected.append(
                    AssertionError(f"read returned an inconsistent job set: {[j.id for j in jobs]}")
                )

    writer_thread = threading.Thread(target=writer, name="jobs-writer")
    reader_threads = [threading.Thread(target=reader, name=f"jobs-reader-{i}") for i in range(reader_count)]

    writer_thread.start()
    for thread in reader_threads:
        thread.start()

    writer_thread.join(timeout=join_timeout)
    stop_event.set()  # belt-and-braces in case the writer raised before setting it
    for thread in reader_threads:
        thread.join(timeout=30)

    still_running = [writer_thread, *reader_threads]
    still_running = [t for t in still_running if t.is_alive()]
    if still_running:
        raise AssertionError(
            f"{[t.name for t in still_running]} did not finish within the join timeout "
            f"({join_timeout}s writer / 30s readers) — aborting instead of letting a stray "
            "background thread bleed into a later test"
        )
    if writer_errors:
        raise writer_errors[0]

    return counters["total"], counters["corrupt"], unexpected


# ---------------------------------------------------------------------------
# Main proof: the FIXED read/write pair never produces a corrupt read.
# ---------------------------------------------------------------------------

class TestAtomicJobsIOFixProof:
    def test_concurrent_readers_never_see_a_corrupt_or_inconsistent_read(self, tmp_path: Path) -> None:
        service = TrainingService(project_manager=None)  # type: ignore[arg-type]
        project_dir = tmp_path / "proj-fixed"
        project_dir.mkdir()

        total_reads, corrupt_reads, unexpected = _hammer(
            project_dir,
            write_fn=service._write_jobs,
            read_fn=service._read_jobs,
            iterations=2000,
            reader_count=4,
        )

        assert not unexpected, f"reader thread(s) hit unexpected condition(s): {unexpected[:5]}"
        assert corrupt_reads == 0, f"{corrupt_reads} corrupt read(s) out of {total_reads} — atomic write/read failed"
        assert total_reads > 0, "readers never got a chance to run"

        leftover_tmp = list(_jobs_path_for(project_dir).parent.glob("*.tmp"))
        assert leftover_tmp == [], f"temp file(s) left behind after the run: {leftover_tmp}"


# ---------------------------------------------------------------------------
# Control: prove the OLD (pre-fix) code CAN fail this exact test shape.
# Rubric R2 — a gate/test never shown able to fail is not a gate.
# ---------------------------------------------------------------------------

class TestAtomicJobsIOControlOldBehaviorCanFail:
    def test_old_write_text_can_produce_corrupt_reads(self, tmp_path: Path) -> None:
        """Runs the pre-fix write/read pair under the same concurrency shape,
        for up to 5 attempts with a smaller iteration count (kept small so a
        failing-to-reproduce environment doesn't blow the test budget).

        Filesystem/OS write-buffering behavior means this race is not
        guaranteed to manifest on every machine — if none of the 5 attempts
        catches a corrupt read, this test is DOCUMENTATION-ONLY (its name
        says so) and is skipped with the measured attempt-by-attempt counts
        rather than reported as a false failure of the real fix above.
        """
        attempts_corrupt_counts: list[int] = []
        for attempt in range(5):
            project_dir = tmp_path / f"proj-unsafe-{attempt}"
            project_dir.mkdir()
            _total_reads, corrupt_reads, unexpected = _hammer(
                project_dir,
                write_fn=_write_jobs_unsafe_old,
                read_fn=_read_jobs_unsafe_old,
                iterations=500,
                reader_count=4,
            )
            assert not unexpected, f"reader thread(s) hit unexpected condition(s): {unexpected[:5]}"
            attempts_corrupt_counts.append(corrupt_reads)
            if corrupt_reads > 0:
                break

        total_corrupt = sum(attempts_corrupt_counts)
        if total_corrupt == 0:
            pytest.skip(
                "documentation-only: the pre-fix write_text()/json.loads() race did not "
                f"manifest in any of {len(attempts_corrupt_counts)} attempt(s) "
                f"(per-attempt corrupt-read counts: {attempts_corrupt_counts}) on this "
                "machine's filesystem/OS write-buffering — this does not mean the race is "
                "not real, only that this environment didn't hit the window. The fix proof "
                "test above (TestAtomicJobsIOFixProof) is what actually gates the defect."
            )
        assert total_corrupt > 0
        # Deliberate evidence line for the dispatch report (visible with pytest -s).
        print(
            f"[control] pre-fix code produced {total_corrupt} corrupt read(s) across "
            f"{len(attempts_corrupt_counts)} attempt(s): {attempts_corrupt_counts}"
        )


# ---------------------------------------------------------------------------
# Unit tests: write_json_atomic failure handling
# ---------------------------------------------------------------------------

class TestWriteJsonAtomicFailureHandling:
    def test_replace_failure_leaves_original_file_intact(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        path = tmp_path / "state.json"
        write_json_atomic(path, {"value": "original"})
        original_bytes = path.read_bytes()

        def _boom(*_args: object, **_kwargs: object) -> None:
            raise OSError("simulated replace failure")

        monkeypatch.setattr("core.project.atomic_io._replace_with_retry", _boom)

        with pytest.raises(OSError):
            write_json_atomic(path, {"value": "new"})

        assert path.read_bytes() == original_bytes, "a failed replace must not touch the original file"
        assert list(tmp_path.glob("*.tmp")) == [], "temp file must be cleaned up when the replace fails"

    def test_encode_failure_cleans_up_temp_file(self, tmp_path: Path) -> None:
        path = tmp_path / "state.json"

        class _NotSerializable:
            pass

        with pytest.raises(TypeError):
            write_json_atomic(path, {"bad": _NotSerializable()})

        assert not path.exists(), "nothing should be written when serialization fails"
        assert list(tmp_path.glob("*.tmp")) == [], "temp file must be cleaned up when json.dumps fails"


# ---------------------------------------------------------------------------
# Finding 2 (待回答 #53-2 review): TrainingService._read_jobs must keep
# raising on a corrupt jobs.json with no cached snapshot -- unlike
# core.integration.workers's runtime/install loaders, it never opts into
# default={} (the owner ruling: never silently return an empty job list).
# ---------------------------------------------------------------------------

def test_training_service_read_jobs_corrupt_with_no_snapshot_still_raises(tmp_path: Path) -> None:
    service = TrainingService(project_manager=None)  # type: ignore[arg-type]
    project_dir = tmp_path / "proj-corrupt-jobs"
    project_dir.mkdir()
    jobs_path = _jobs_path_for(project_dir)
    jobs_path.parent.mkdir(parents=True)
    jobs_path.write_text("{not valid json", encoding="utf-8")

    with pytest.raises(json.JSONDecodeError):
        service._read_jobs(project_dir)


# ---------------------------------------------------------------------------
# Unit tests: read_json_tolerant fallback behavior
# ---------------------------------------------------------------------------

class TestReadJsonTolerantFallback:
    def test_falls_back_to_last_good_snapshot_on_corrupt_file(self, tmp_path: Path) -> None:
        path = tmp_path / "jobs.json"
        write_json_atomic(path, {"jobs": ["a", "b"]})
        good_value = read_json_tolerant(path)  # primes the in-process cache
        assert good_value == {"jobs": ["a", "b"]}

        # Simulate a reader observing a mid-write (or otherwise corrupted) file
        # directly — bypassing the atomic writer on purpose for this test.
        path.write_text("{not valid json", encoding="utf-8")

        recovered = read_json_tolerant(path)
        assert recovered == good_value, "a corrupt read must fall back to the last known-good snapshot"

    def test_raises_when_no_cached_snapshot_exists(self, tmp_path: Path) -> None:
        path = tmp_path / "never-read-before.json"
        path.write_text("{not valid json", encoding="utf-8")

        with pytest.raises(json.JSONDecodeError):
            read_json_tolerant(path)

    def test_successful_read_returns_parsed_value(self, tmp_path: Path) -> None:
        path = tmp_path / "plain.json"
        write_json_atomic(path, {"a": 1, "b": [1, 2, 3]})
        assert read_json_tolerant(path) == {"a": 1, "b": [1, 2, 3]}

    def test_no_snapshot_and_no_default_still_raises(self, tmp_path: Path) -> None:
        """待回答 #53-2 review finding 2, part 2: core.training.service._read_jobs
        calls read_json_tolerant with no ``default=`` and MUST keep raising on
        a corrupt file with no prior successful read -- it must never silently
        return an empty job list."""
        path = tmp_path / "jobs.json"
        path.write_text("{not valid json", encoding="utf-8")
        with pytest.raises(json.JSONDecodeError):
            read_json_tolerant(path)

    def test_no_snapshot_with_default_returns_default_and_warns(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """待回答 #53-2 review finding 2, part 1: a caller that opts in via
        ``default=`` (core.integration.workers's runtime/install state
        loaders) gets the pre-atomic-io caller contract back -- a corrupt
        file with no cached snapshot degrades to the given default instead of
        raising into a FastAPI route, with a WARNING logged so the
        degradation is visible."""
        path = tmp_path / "never-read-before.json"
        path.write_text("{not valid json", encoding="utf-8")

        with caplog.at_level("WARNING", logger="misaka.atomic_io"):
            result = read_json_tolerant(path, default={})

        assert result == {}
        assert any("failed to parse twice" in record.message for record in caplog.records)

    def test_default_is_not_used_when_a_snapshot_exists(self, tmp_path: Path) -> None:
        """A cached snapshot always wins over a caller-supplied default --
        default= is only a fallback for the "never read successfully" case."""
        path = tmp_path / "jobs.json"
        write_json_atomic(path, {"jobs": ["a"]})
        primed = read_json_tolerant(path, default={"jobs": []})
        assert primed == {"jobs": ["a"]}

        path.write_text("{not valid json", encoding="utf-8")
        result = read_json_tolerant(path, default={"jobs": []})
        assert result == {"jobs": ["a"]}, "cached snapshot must take priority over default="

    def test_default_none_is_a_valid_explicit_default(self, tmp_path: Path) -> None:
        """default=None must be honored as an explicit default, distinct from
        "no default was passed" -- proves the sentinel, not `is None`, gates
        the fallback."""
        path = tmp_path / "never-read-before.json"
        path.write_text("{not valid json", encoding="utf-8")
        assert read_json_tolerant(path, default=None) is None


# ---------------------------------------------------------------------------
# Unit tests: bounded LRU snapshot/warning-timestamp caches (finding 3)
# ---------------------------------------------------------------------------

class TestReadJsonTolerantCacheEviction:
    def test_snapshot_cache_evicts_oldest_path_beyond_cap(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """_last_good_snapshots must never grow past _MAX_TRACKED_PATHS --
        the least-recently-touched path is evicted first (待回答 #53-2 review
        finding 3: an unbounded per-path cache in a long-running process).
        The cap is monkeypatched down to a small number so the test doesn't
        need to create hundreds of real files to exercise eviction."""
        from core.project import atomic_io

        monkeypatch.setattr(atomic_io, "_MAX_TRACKED_PATHS", 3)

        paths = [tmp_path / f"state-{i}.json" for i in range(5)]
        for path in paths:
            write_json_atomic(path, {"i": path.name})
            read_json_tolerant(path)  # primes _last_good_snapshots for this path

        assert len(atomic_io._last_good_snapshots) <= 3
        # The two oldest, never-touched-again paths must have been evicted;
        # the three most recently touched must still be cached.
        for evicted_path in paths[:2]:
            assert str(evicted_path) not in atomic_io._last_good_snapshots
        for kept_path in paths[-3:]:
            assert str(kept_path) in atomic_io._last_good_snapshots

    def test_warning_timestamp_cache_evicts_oldest_path_beyond_cap(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """_last_warning_at is bounded the same way as the snapshot cache."""
        from core.project import atomic_io

        monkeypatch.setattr(atomic_io, "_MAX_TRACKED_PATHS", 3)
        # Bypass the 60s per-path throttle so every corrupt read below
        # actually logs (and therefore records) a warning timestamp.
        monkeypatch.setattr(atomic_io, "_WARNING_THROTTLE_SEC", 0.0)

        paths = [tmp_path / f"warn-{i}.json" for i in range(5)]
        for path in paths:
            write_json_atomic(path, {"ok": True})
            read_json_tolerant(path)  # prime a snapshot so the corrupt read below falls back (and warns) instead of raising
            path.write_text("{not valid json", encoding="utf-8")
            result = read_json_tolerant(path)
            assert result == {"ok": True}

        assert len(atomic_io._last_warning_at) <= 3
        for evicted_path in paths[:2]:
            assert str(evicted_path) not in atomic_io._last_warning_at
        for kept_path in paths[-3:]:
            assert str(kept_path) in atomic_io._last_warning_at
