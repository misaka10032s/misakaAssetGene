"""Tests for core/consultant/fidelity_store.py (spec §5 persistence)."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from core.consultant.fidelity_store import FidelityStore
from core.models.schemas import FidelityCheckResult, FidelityLoopStatus


def _result(id: str, passed: bool, bbox: tuple[int, int, int, int] | None = None) -> FidelityCheckResult:
    return FidelityCheckResult(id=id, passed=passed, confidence=0.8, region_bbox=bbox, note="n")


class TestCreateAndReadLoop:
    def test_create_loop_defaults(self, tmp_path: Path) -> None:
        store = FidelityStore(tmp_path / "memory.sqlite")
        loop = store.create_loop(
            project_id="proj-1",
            root_asset_id="asset-root",
            character_sheet_id="sheet-1",
            outfit_variant="default",
            max_rounds=4,
            auto_continue=False,
        )
        assert loop.status is FidelityLoopStatus.PENDING_CRITIQUE
        assert loop.current_round == 0
        assert loop.best_asset_id == "asset-root"
        assert loop.best_pass_count == 0
        assert loop.auto_continue is False

        fetched = store.get_loop("proj-1", loop.id)
        assert fetched is not None
        assert fetched.id == loop.id
        assert fetched.character_sheet_id == "sheet-1"
        assert fetched.outfit_variant == "default"

    def test_get_loop_wrong_project_returns_none(self, tmp_path: Path) -> None:
        store = FidelityStore(tmp_path / "memory.sqlite")
        loop = store.create_loop(
            project_id="proj-1", root_asset_id="a", character_sheet_id="s",
            outfit_variant="default", max_rounds=4, auto_continue=False,
        )
        assert store.get_loop("proj-2", loop.id) is None

    def test_get_unknown_loop_returns_none(self, tmp_path: Path) -> None:
        store = FidelityStore(tmp_path / "memory.sqlite")
        assert store.get_loop("proj-1", "nope") is None

    def test_list_loops_scoped_to_project(self, tmp_path: Path) -> None:
        store = FidelityStore(tmp_path / "memory.sqlite")
        store.create_loop(
            project_id="proj-1", root_asset_id="a", character_sheet_id="s",
            outfit_variant="default", max_rounds=4, auto_continue=False,
        )
        store.create_loop(
            project_id="proj-2", root_asset_id="b", character_sheet_id="s",
            outfit_variant="default", max_rounds=4, auto_continue=False,
        )
        assert len(store.list_loops("proj-1")) == 1
        assert len(store.list_loops("proj-2")) == 1


class TestSaveLoop:
    def test_save_persists_status_and_bookkeeping(self, tmp_path: Path) -> None:
        store = FidelityStore(tmp_path / "memory.sqlite")
        loop = store.create_loop(
            project_id="proj-1", root_asset_id="asset-root", character_sheet_id="s",
            outfit_variant="default", max_rounds=4, auto_continue=True,
        )
        loop.status = FidelityLoopStatus.CRITIQUING
        loop.current_round = 1
        loop.best_asset_id = "asset-2"
        loop.best_pass_count = 17
        saved = store.save_loop(loop)
        assert saved.updated_at >= loop.created_at

        fetched = store.get_loop("proj-1", loop.id)
        assert fetched is not None
        assert fetched.status is FidelityLoopStatus.CRITIQUING
        assert fetched.current_round == 1
        assert fetched.best_asset_id == "asset-2"
        assert fetched.best_pass_count == 17
        assert fetched.auto_continue is True


class TestRounds:
    def test_append_and_list_rounds(self, tmp_path: Path) -> None:
        store = FidelityStore(tmp_path / "memory.sqlite")
        loop = store.create_loop(
            project_id="proj-1", root_asset_id="a", character_sheet_id="s",
            outfit_variant="default", max_rounds=4, auto_continue=False,
        )
        results = [_result("c1", True), _result("c2", False, bbox=(1, 2, 3, 4))]
        round0 = store.append_round(
            loop_id=loop.id, round_index=0, asset_id="a",
            critic_results=results, pass_count=1, fail_count=1,
        )
        round1 = store.append_round(
            loop_id=loop.id, round_index=1, asset_id="asset-2",
            critic_results=[_result("c1", True), _result("c2", True)],
            pass_count=2, fail_count=0,
            mask_asset_id="mask-1", refine_job_id="job-1",
        )
        rounds = store.list_rounds(loop.id)
        assert [r.round_index for r in rounds] == [0, 1]
        assert rounds[0].id == round0.id
        assert rounds[0].pass_count == 1
        assert rounds[0].fail_count == 1
        assert rounds[0].critic_results[1].region_bbox == (1, 2, 3, 4)
        assert rounds[1].mask_asset_id == "mask-1"
        assert rounds[1].refine_job_id == "job-1"
        assert round1.critic_results[0].passed is True

    def test_latest_round(self, tmp_path: Path) -> None:
        store = FidelityStore(tmp_path / "memory.sqlite")
        loop = store.create_loop(
            project_id="proj-1", root_asset_id="a", character_sheet_id="s",
            outfit_variant="default", max_rounds=4, auto_continue=False,
        )
        store.append_round(loop_id=loop.id, round_index=0, asset_id="a", critic_results=[], pass_count=0, fail_count=0)
        store.append_round(loop_id=loop.id, round_index=1, asset_id="b", critic_results=[], pass_count=1, fail_count=0)
        latest = store.latest_round(loop.id)
        assert latest is not None
        assert latest.round_index == 1
        assert latest.asset_id == "b"

    def test_latest_round_none_when_no_rounds(self, tmp_path: Path) -> None:
        store = FidelityStore(tmp_path / "memory.sqlite")
        assert store.latest_round("no-such-loop") is None


class TestClaimRound:
    """C2-review.md MAJOR #1 — atomic ``advance()`` claim."""

    def test_claim_succeeds_from_allowed_status(self, tmp_path: Path) -> None:
        store = FidelityStore(tmp_path / "memory.sqlite")
        loop = store.create_loop(
            project_id="proj-1", root_asset_id="a", character_sheet_id="s",
            outfit_variant="default", max_rounds=4, auto_continue=False,
        )
        loop.status = FidelityLoopStatus.AWAITING_USER
        store.save_loop(loop)

        claimed = store.claim_round(
            "proj-1", loop.id,
            frozenset({FidelityLoopStatus.AWAITING_USER, FidelityLoopStatus.STOPPED_REGRESSION_RECOVERED}),
            FidelityLoopStatus.BUILDING_MASK,
        )
        assert claimed is True
        fetched = store.get_loop("proj-1", loop.id)
        assert fetched is not None
        assert fetched.status is FidelityLoopStatus.BUILDING_MASK

    def test_second_claim_fails_once_status_already_changed(self, tmp_path: Path) -> None:
        """Simulates two concurrent ``advance()`` callers: the first claim
        moves the row OUT of the allowed set, so a second claim attempt
        against the same allowed set must fail (``rowcount == 0``) rather
        than silently re-claiming an already-claimed round."""
        store = FidelityStore(tmp_path / "memory.sqlite")
        loop = store.create_loop(
            project_id="proj-1", root_asset_id="a", character_sheet_id="s",
            outfit_variant="default", max_rounds=4, auto_continue=False,
        )
        loop.status = FidelityLoopStatus.AWAITING_USER
        store.save_loop(loop)

        allowed = frozenset({FidelityLoopStatus.AWAITING_USER, FidelityLoopStatus.STOPPED_REGRESSION_RECOVERED})
        first = store.claim_round("proj-1", loop.id, allowed, FidelityLoopStatus.BUILDING_MASK)
        second = store.claim_round("proj-1", loop.id, allowed, FidelityLoopStatus.BUILDING_MASK)
        assert first is True
        assert second is False

    def test_claim_fails_on_unknown_loop(self, tmp_path: Path) -> None:
        store = FidelityStore(tmp_path / "memory.sqlite")
        claimed = store.claim_round(
            "proj-1", "no-such-loop", frozenset({FidelityLoopStatus.AWAITING_USER}),
            FidelityLoopStatus.BUILDING_MASK,
        )
        assert claimed is False


class TestLastError:
    def test_save_and_read_last_error(self, tmp_path: Path) -> None:
        store = FidelityStore(tmp_path / "memory.sqlite")
        loop = store.create_loop(
            project_id="proj-1", root_asset_id="a", character_sheet_id="s",
            outfit_variant="default", max_rounds=4, auto_continue=False,
        )
        assert loop.last_error is None

        loop.status = FidelityLoopStatus.FAILED
        loop.last_error = "VLM critic exploded: connection refused"
        store.save_loop(loop)

        fetched = store.get_loop("proj-1", loop.id)
        assert fetched is not None
        assert fetched.status is FidelityLoopStatus.FAILED
        assert fetched.last_error == "VLM critic exploded: connection refused"

    def test_opens_against_db_missing_the_last_error_column(self, tmp_path: Path) -> None:
        """Simulate a ``memory.sqlite`` created by the ORIGINAL Brief-2
        schema (before C2-review.md MAJOR #2 added ``last_error``) —
        ``FidelityStore`` must migrate the column in without disturbing
        existing rows (same idiom as ``TestMigrationSafety`` below)."""
        db_path = tmp_path / "memory.sqlite"
        now = datetime.now(UTC).isoformat()
        conn = sqlite3.connect(db_path)
        conn.execute(
            "CREATE TABLE fidelity_loops ("
            "id TEXT PRIMARY KEY, project_id TEXT NOT NULL, root_asset_id TEXT NOT NULL, "
            "character_sheet_id TEXT NOT NULL, outfit_variant TEXT NOT NULL, status TEXT NOT NULL, "
            "current_round INTEGER NOT NULL DEFAULT 0, max_rounds INTEGER NOT NULL DEFAULT 4, "
            "best_asset_id TEXT NOT NULL, best_pass_count INTEGER NOT NULL DEFAULT 0, "
            "auto_continue INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)"
        )
        conn.execute(
            "INSERT INTO fidelity_loops (id, project_id, root_asset_id, character_sheet_id, "
            "outfit_variant, status, best_asset_id, created_at, updated_at) "
            "VALUES ('loop-1', 'proj-1', 'a', 's', 'default', 'awaiting_user', 'a', ?, ?)",
            (now, now),
        )
        conn.commit()
        conn.close()

        store = FidelityStore(db_path)
        fetched = store.get_loop("proj-1", "loop-1")
        assert fetched is not None
        assert fetched.last_error is None  # back-filled NULL, not a crash

        loop = store.create_loop(
            project_id="proj-1", root_asset_id="b", character_sheet_id="s",
            outfit_variant="default", max_rounds=4, auto_continue=False,
        )
        assert loop.last_error is None


class TestMode:
    """C4 gap-fix (fidelity-modes, 2026-09-06) — ``mode`` persistence."""

    def test_create_loop_defaults_mode_to_default(self, tmp_path: Path) -> None:
        store = FidelityStore(tmp_path / "memory.sqlite")
        loop = store.create_loop(
            project_id="proj-1", root_asset_id="a", character_sheet_id="s",
            outfit_variant="default", max_rounds=4, auto_continue=False,
        )
        assert loop.mode == "default"
        fetched = store.get_loop("proj-1", loop.id)
        assert fetched is not None
        assert fetched.mode == "default"

    def test_create_loop_persists_non_default_mode_round_trip(self, tmp_path: Path) -> None:
        store = FidelityStore(tmp_path / "memory.sqlite")
        loop = store.create_loop(
            project_id="proj-1", root_asset_id="a", character_sheet_id="s",
            outfit_variant="default", max_rounds=4, auto_continue=False, mode="combat",
        )
        assert loop.mode == "combat"

        fetched = store.get_loop("proj-1", loop.id)
        assert fetched is not None
        assert fetched.mode == "combat"

        # save_loop() must never clobber the mode a loop was created with.
        fetched.status = FidelityLoopStatus.CRITIQUING
        store.save_loop(fetched)
        refetched = store.get_loop("proj-1", loop.id)
        assert refetched is not None
        assert refetched.mode == "combat"

    def test_opens_against_db_missing_the_mode_column(self, tmp_path: Path) -> None:
        """Simulate a ``memory.sqlite`` created BEFORE the ``mode`` column
        existed (the original Brief-2 schema + the later ``last_error``
        column, but no ``mode``) — ``FidelityStore`` must migrate it in
        without disturbing existing rows, same idiom as
        ``TestLastError.test_opens_against_db_missing_the_last_error_column``."""
        db_path = tmp_path / "memory.sqlite"
        now = datetime.now(UTC).isoformat()
        conn = sqlite3.connect(db_path)
        conn.execute(
            "CREATE TABLE fidelity_loops ("
            "id TEXT PRIMARY KEY, project_id TEXT NOT NULL, root_asset_id TEXT NOT NULL, "
            "character_sheet_id TEXT NOT NULL, outfit_variant TEXT NOT NULL, status TEXT NOT NULL, "
            "current_round INTEGER NOT NULL DEFAULT 0, max_rounds INTEGER NOT NULL DEFAULT 4, "
            "best_asset_id TEXT NOT NULL, best_pass_count INTEGER NOT NULL DEFAULT 0, "
            "auto_continue INTEGER NOT NULL DEFAULT 0, last_error TEXT, "
            "created_at TEXT NOT NULL, updated_at TEXT NOT NULL)"
        )
        conn.execute(
            "INSERT INTO fidelity_loops (id, project_id, root_asset_id, character_sheet_id, "
            "outfit_variant, status, best_asset_id, created_at, updated_at) "
            "VALUES ('loop-1', 'proj-1', 'a', 's', 'default', 'awaiting_user', 'a', ?, ?)",
            (now, now),
        )
        conn.commit()
        conn.close()

        store = FidelityStore(db_path)
        fetched = store.get_loop("proj-1", "loop-1")
        assert fetched is not None
        assert fetched.mode == "default"  # back-filled default, not a crash

        loop = store.create_loop(
            project_id="proj-1", root_asset_id="b", character_sheet_id="s",
            outfit_variant="default", max_rounds=4, auto_continue=False,
        )
        assert loop.mode == "default"


class TestMigrationSafety:
    def test_opens_against_db_missing_the_new_tables(self, tmp_path: Path) -> None:
        """Simulate an OLDER memory.sqlite (as AssetStore/SessionStore would
        have created, pre-Brief-2) that has no fidelity_* tables at all —
        FidelityStore must add them without disturbing anything else."""
        db_path = tmp_path / "memory.sqlite"
        conn = sqlite3.connect(db_path)
        conn.execute(
            "CREATE TABLE character_sheets (id TEXT PRIMARY KEY, project_id TEXT NOT NULL, name TEXT NOT NULL)"
        )
        conn.execute("INSERT INTO character_sheets (id, project_id, name) VALUES ('cs-1', 'proj-1', 'Test')")
        conn.commit()
        conn.close()

        store = FidelityStore(db_path)
        loop = store.create_loop(
            project_id="proj-1", root_asset_id="a", character_sheet_id="cs-1",
            outfit_variant="default", max_rounds=4, auto_continue=False,
        )
        assert store.get_loop("proj-1", loop.id) is not None

        # Pre-existing table + row must survive untouched.
        conn = sqlite3.connect(db_path)
        row = conn.execute("SELECT name FROM character_sheets WHERE id='cs-1'").fetchone()
        conn.close()
        assert row == ("Test",)
