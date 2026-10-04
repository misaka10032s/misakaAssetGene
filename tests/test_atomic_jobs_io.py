"""Interleaving proof + unit tests for 待回答 #53 item 2 (2026-09-08 ruling).

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

Both proofs are a deterministic interleaving on ONE thread: a reader is called
at the exact instant between the writer's two steps, so no thread, no timing
and no attempt loop is involved and the outcome is the same on every run.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path

import pytest

from core.models.schemas import Modality, TrainingJob, TrainingJobStatus
from core.project import atomic_io
from core.project.atomic_io import read_json_tolerant, write_json_atomic
from core.training.service import TrainingService

# ---------------------------------------------------------------------------
# Shared payload helpers
# ---------------------------------------------------------------------------

# Fixed instant for every seeded record: no test here reads the real clock.
FIXED_NOW = datetime(2026, 1, 15, 12, 0, tzinfo=UTC)

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
    now = FIXED_NOW
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


# ---------------------------------------------------------------------------
# Main proof: the FIXED write never exposes a corrupt or inconsistent document.
# ---------------------------------------------------------------------------

class TestAtomicJobsIOFixProof:
    def test_reader_between_temp_write_and_replace_sees_the_whole_previous_document(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        service = TrainingService(project_manager=None)  # type: ignore[arg-type]
        project_dir = tmp_path / "proj-fixed"
        project_dir.mkdir()
        # The previous, complete document (revision 1) is already on disk.
        service._write_jobs(project_dir, _build_realistic_jobs(revision=1))

        real_replace = os.replace
        seen_by_reader: list[list[TrainingJob]] = []

        def reader_runs_just_before_the_replace(src: object, dst: object) -> None:
            # The new document exists only as the temp file; the destination
            # must still be the whole previous document.  The strict reader
            # (no retry, no fallback) raises on any partial document.
            seen_by_reader.append(_read_jobs_unsafe_old(project_dir))
            real_replace(src, dst)

        monkeypatch.setattr(atomic_io.os, "replace", reader_runs_just_before_the_replace)

        service._write_jobs(project_dir, _build_realistic_jobs(revision=2))

        assert len(seen_by_reader) == 1, "the write must replace the destination exactly once"
        previous = seen_by_reader[0]
        assert len(previous) == _JOB_COUNT
        assert {job.id for job in previous} == set(_job_ids())
        assert all("(rev 1)" in job.title for job in previous), "reader must see the whole OLD document"

        after = service._read_jobs(project_dir)
        assert len(after) == _JOB_COUNT
        assert all("(rev 2)" in job.title for job in after), "after the replace the whole NEW document is read"

        leftover_tmp = list(_jobs_path_for(project_dir).parent.glob("*.tmp"))
        assert leftover_tmp == [], f"temp file(s) left behind after the run: {leftover_tmp}"


# ---------------------------------------------------------------------------
# Control: prove the OLD (pre-fix) code CAN fail this exact test shape.
# Rubric R2 — a gate/test never shown able to fail is not a gate.
# ---------------------------------------------------------------------------

class TestAtomicJobsIOControlOldBehaviorCanFail:
    def test_old_write_text_exposes_a_partial_document_to_a_reader(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The pre-fix write_text()/json.loads() pair, interleaved
        deterministically: ``Path.write_text`` is replaced by a wrapper that
        writes the first half of the text, calls the strict reader, then writes
        the rest.  The reader's parse fails with ``json.JSONDecodeError`` —
        exactly once, on every run — which is the defect the atomic write
        removes."""
        project_dir = tmp_path / "proj-unsafe"
        project_dir.mkdir()
        _write_jobs_unsafe_old(project_dir, _build_realistic_jobs(revision=1))

        reader_outcomes: list[object] = []

        def write_text_with_reader_in_the_middle(
            self: Path,
            data: str,
            encoding: str | None = None,
            errors: str | None = None,
            newline: str | None = None,
        ) -> int:
            half = len(data) // 2
            with self.open("w", encoding=encoding) as handle:
                handle.write(data[:half])
                handle.flush()
                try:
                    reader_outcomes.append(_read_jobs_unsafe_old(project_dir))
                except json.JSONDecodeError as error:
                    reader_outcomes.append(error)
                handle.write(data[half:])
            return len(data)

        monkeypatch.setattr(Path, "write_text", write_text_with_reader_in_the_middle)

        _write_jobs_unsafe_old(project_dir, _build_realistic_jobs(revision=2))

        assert len(reader_outcomes) == 1
        assert isinstance(reader_outcomes[0], json.JSONDecodeError)


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
