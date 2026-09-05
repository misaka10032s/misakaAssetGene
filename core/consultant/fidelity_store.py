"""SQLite-backed persistence for the fidelity refine loop (spec §5).

Mirrors ``core.consultant.session_store.SessionStore`` / ``core.training.
asset_store.AssetStore``'s exact connection pattern (WAL journal mode,
5-second busy timeout, ``CREATE TABLE IF NOT EXISTS`` schema) against the
SAME per-project ``memory.sqlite`` file those stores already use — a
``FidelityStore`` instance and an ``AssetStore``/``SessionStore`` instance
pointed at the same path simply add their own tables to the one file.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import UTC, datetime
from pathlib import Path

from core.models.schemas import FidelityCheckResult, FidelityLoop, FidelityLoopRound, FidelityLoopStatus

_SCHEMA = """
CREATE TABLE IF NOT EXISTS fidelity_loops (
    id                  TEXT PRIMARY KEY,
    project_id          TEXT NOT NULL,
    root_asset_id       TEXT NOT NULL,
    character_sheet_id  TEXT NOT NULL,
    outfit_variant      TEXT NOT NULL,
    status              TEXT NOT NULL,
    current_round       INTEGER NOT NULL DEFAULT 0,
    max_rounds          INTEGER NOT NULL DEFAULT 4,
    best_asset_id       TEXT NOT NULL,
    best_pass_count     INTEGER NOT NULL DEFAULT 0,
    auto_continue       INTEGER NOT NULL DEFAULT 0,
    mode                TEXT NOT NULL DEFAULT 'default',
    last_error          TEXT,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_fidelity_loops_project ON fidelity_loops(project_id);

CREATE TABLE IF NOT EXISTS fidelity_loop_rounds (
    id              TEXT PRIMARY KEY,
    loop_id         TEXT NOT NULL,
    round_index     INTEGER NOT NULL,
    asset_id        TEXT NOT NULL,
    critic_json     TEXT NOT NULL DEFAULT '[]',
    pass_count      INTEGER NOT NULL DEFAULT 0,
    fail_count      INTEGER NOT NULL DEFAULT 0,
    mask_asset_id   TEXT,
    refine_job_id   TEXT,
    created_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_fidelity_rounds_loop ON fidelity_loop_rounds(loop_id);
"""


def _now() -> datetime:
    return datetime.now(UTC)


def _new_id() -> str:
    return uuid.uuid4().hex


class FidelityStore:
    """Repository for ``fidelity_loops`` / ``fidelity_loop_rounds`` in a
    project's ``memory.sqlite`` (spec §5). Migration-safe: ``CREATE TABLE IF
    NOT EXISTS`` means opening this store against an OLDER ``memory.sqlite``
    that predates these two tables simply adds them alongside whatever
    ``AssetStore``/``SessionStore`` already created there — no data loss, no
    separate migration script needed (unlike ``AssetStore``'s
    ``_migrate_character_sheets``, there is no pre-existing column shape to
    reconcile here since these tables are entirely new)."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=5.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(_SCHEMA)
            self._migrate_last_error(conn)
            self._migrate_mode(conn)

    def _migrate_last_error(self, conn: sqlite3.Connection) -> None:
        """Add ``last_error`` to a pre-existing ``fidelity_loops`` table
        (C2-review.md MAJOR #2 — added after the original Brief-2 schema
        shipped). ``CREATE TABLE IF NOT EXISTS`` above only applies to a
        brand-new DB file; a ``memory.sqlite`` created before this column
        existed keeps its original column set forever unless migrated here,
        same idiom as ``AssetStore._migrate_character_sheets``. Existing rows
        back-fill as ``NULL`` -> ``last_error=None``, which is the correct
        "no failure recorded" default."""
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(fidelity_loops)")}
        if "last_error" not in columns:
            conn.execute("ALTER TABLE fidelity_loops ADD COLUMN last_error TEXT")

    def _migrate_mode(self, conn: sqlite3.Connection) -> None:
        """Add ``mode`` to a pre-existing ``fidelity_loops`` table (C4
        gap-fix, fidelity-modes 2026-09-06 — ``FidelityLoopStartRequest.mode``
        was only ever honoured within the SAME ``start_loop()`` call because
        this column never existed; see ``FidelityLoop.mode`` docstring).
        Same idiom as ``_migrate_last_error`` above: a ``memory.sqlite``
        created before this column existed keeps its original column set
        forever unless migrated here. Existing rows back-fill as
        ``'default'`` — the correct behavior for every loop started before
        modes existed."""
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(fidelity_loops)")}
        if "mode" not in columns:
            conn.execute("ALTER TABLE fidelity_loops ADD COLUMN mode TEXT NOT NULL DEFAULT 'default'")

    # ------------------------------------------------------------------
    # FidelityLoop
    # ------------------------------------------------------------------

    def create_loop(
        self,
        *,
        project_id: str,
        root_asset_id: str,
        character_sheet_id: str,
        outfit_variant: str,
        max_rounds: int,
        auto_continue: bool,
        mode: str = "default",
        status: FidelityLoopStatus = FidelityLoopStatus.PENDING_CRITIQUE,
    ) -> FidelityLoop:
        now = _now()
        loop = FidelityLoop(
            id=_new_id(),
            project_id=project_id,
            root_asset_id=root_asset_id,
            character_sheet_id=character_sheet_id,
            outfit_variant=outfit_variant,
            status=status,
            current_round=0,
            max_rounds=max_rounds,
            best_asset_id=root_asset_id,
            best_pass_count=0,
            auto_continue=auto_continue,
            mode=mode,
            created_at=now,
            updated_at=now,
        )
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO fidelity_loops "
                "(id, project_id, root_asset_id, character_sheet_id, outfit_variant, status, "
                " current_round, max_rounds, best_asset_id, best_pass_count, auto_continue, mode, "
                " created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    loop.id, loop.project_id, loop.root_asset_id, loop.character_sheet_id,
                    loop.outfit_variant, loop.status.value, loop.current_round, loop.max_rounds,
                    loop.best_asset_id, loop.best_pass_count, int(loop.auto_continue), loop.mode,
                    loop.created_at.isoformat(), loop.updated_at.isoformat(),
                ),
            )
        return loop

    def get_loop(self, project_id: str, loop_id: str) -> FidelityLoop | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM fidelity_loops WHERE id=? AND project_id=?",
                (loop_id, project_id),
            ).fetchone()
        return self._row_to_loop(row) if row else None

    def list_loops(self, project_id: str) -> list[FidelityLoop]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM fidelity_loops WHERE project_id=? ORDER BY created_at",
                (project_id,),
            ).fetchall()
        return [self._row_to_loop(row) for row in rows]

    def save_loop(self, loop: FidelityLoop) -> FidelityLoop:
        """Persist the full loop record, bumping ``updated_at``."""
        loop.updated_at = _now()
        with self._connect() as conn:
            conn.execute(
                "UPDATE fidelity_loops SET status=?, current_round=?, max_rounds=?, "
                "best_asset_id=?, best_pass_count=?, auto_continue=?, last_error=?, updated_at=? "
                "WHERE id=? AND project_id=?",
                (
                    loop.status.value, loop.current_round, loop.max_rounds,
                    loop.best_asset_id, loop.best_pass_count, int(loop.auto_continue),
                    loop.last_error, loop.updated_at.isoformat(), loop.id, loop.project_id,
                ),
            )
        return loop

    def claim_round(
        self,
        project_id: str,
        loop_id: str,
        allowed_statuses: frozenset[FidelityLoopStatus] | set[FidelityLoopStatus],
        claim_status: FidelityLoopStatus,
    ) -> bool:
        """Atomically transition ``status`` from one of ``allowed_statuses``
        to ``claim_status`` in a single ``UPDATE ... WHERE status IN (...)``
        (C2-review.md MAJOR #1 — ``advance()``'s prior check-then-act race:
        two concurrent callers could both observe an awaiting status before
        either wrote back, running the same refine round twice). Returns
        ``True`` iff exactly one row matched and was updated — the caller
        holds the exclusive right to run the round; ``False`` means the row
        was missing or another caller already claimed/changed it first, and
        the caller must NOT proceed."""
        now = _now().isoformat()
        statuses = list(allowed_statuses)
        placeholders = ",".join("?" for _ in statuses)
        with self._connect() as conn:
            cursor = conn.execute(
                f"UPDATE fidelity_loops SET status=?, updated_at=? "
                f"WHERE id=? AND project_id=? AND status IN ({placeholders})",
                (claim_status.value, now, loop_id, project_id, *(status.value for status in statuses)),
            )
            return cursor.rowcount == 1

    def _row_to_loop(self, row: sqlite3.Row) -> FidelityLoop:
        return FidelityLoop(
            id=row["id"],
            project_id=row["project_id"],
            root_asset_id=row["root_asset_id"],
            character_sheet_id=row["character_sheet_id"],
            outfit_variant=row["outfit_variant"],
            status=FidelityLoopStatus(row["status"]),
            current_round=row["current_round"],
            max_rounds=row["max_rounds"],
            best_asset_id=row["best_asset_id"],
            best_pass_count=row["best_pass_count"],
            auto_continue=bool(row["auto_continue"]),
            mode=row["mode"],
            last_error=row["last_error"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    # ------------------------------------------------------------------
    # FidelityLoopRound
    # ------------------------------------------------------------------

    def append_round(
        self,
        *,
        loop_id: str,
        round_index: int,
        asset_id: str,
        critic_results: list[FidelityCheckResult],
        pass_count: int,
        fail_count: int,
        mask_asset_id: str | None = None,
        refine_job_id: str | None = None,
    ) -> FidelityLoopRound:
        round_record = FidelityLoopRound(
            id=_new_id(),
            loop_id=loop_id,
            round_index=round_index,
            asset_id=asset_id,
            critic_results=critic_results,
            pass_count=pass_count,
            fail_count=fail_count,
            mask_asset_id=mask_asset_id,
            refine_job_id=refine_job_id,
            created_at=_now(),
        )
        critic_json = json.dumps(
            [result.model_dump(mode="json") for result in critic_results], ensure_ascii=False
        )
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO fidelity_loop_rounds "
                "(id, loop_id, round_index, asset_id, critic_json, pass_count, fail_count, "
                " mask_asset_id, refine_job_id, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    round_record.id, round_record.loop_id, round_record.round_index,
                    round_record.asset_id, critic_json, round_record.pass_count,
                    round_record.fail_count, round_record.mask_asset_id,
                    round_record.refine_job_id, round_record.created_at.isoformat(),
                ),
            )
        return round_record

    def list_rounds(self, loop_id: str) -> list[FidelityLoopRound]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM fidelity_loop_rounds WHERE loop_id=? ORDER BY round_index",
                (loop_id,),
            ).fetchall()
        return [self._row_to_round(row) for row in rows]

    def latest_round(self, loop_id: str) -> FidelityLoopRound | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM fidelity_loop_rounds WHERE loop_id=? ORDER BY round_index DESC LIMIT 1",
                (loop_id,),
            ).fetchone()
        return self._row_to_round(row) if row else None

    def _row_to_round(self, row: sqlite3.Row) -> FidelityLoopRound:
        raw_results = json.loads(row["critic_json"] or "[]")
        return FidelityLoopRound(
            id=row["id"],
            loop_id=row["loop_id"],
            round_index=row["round_index"],
            asset_id=row["asset_id"],
            critic_results=[FidelityCheckResult(**item) for item in raw_results],
            pass_count=row["pass_count"],
            fail_count=row["fail_count"],
            mask_asset_id=row["mask_asset_id"],
            refine_job_id=row["refine_job_id"],
            created_at=datetime.fromisoformat(row["created_at"]),
        )
