"""SQLite-backed consultant session repository (spec §4.1.1).

Consultant session state is persisted server-side in each project's
``memory.sqlite`` in a dedicated ``sessions`` table so that the "loop until the
checklist is complete" flow survives application restarts. A new
:class:`SessionStore` instance pointed at the same file restores prior state.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from core.models.schemas import (
    ClarifyResult,
    ConsultantAnalysis,
    ConsultantSession,
    ConsultantState,
    Modality,
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    session_id      TEXT PRIMARY KEY,
    project_id      TEXT NOT NULL,
    modality        TEXT,
    state           TEXT NOT NULL,
    checklist_status TEXT NOT NULL DEFAULT '{}',
    slots           TEXT NOT NULL DEFAULT '{}',
    plan            TEXT,
    last_result     TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sessions_project ON sessions(project_id);
"""


def _now() -> datetime:
    return datetime.now(timezone.utc)


class SessionStore:
    """Repository over the ``sessions`` table in a project's ``memory.sqlite``."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        return connection

    def _init_schema(self) -> None:
        with self._connect() as connection:
            connection.executescript(_SCHEMA)

    def create(
        self,
        *,
        session_id: str,
        project_id: str,
        modality: Modality | None,
        state: ConsultantState = ConsultantState.INTAKE,
    ) -> ConsultantSession:
        """Insert a new session row and return it."""
        now = _now()
        session = ConsultantSession(
            session_id=session_id,
            project_id=project_id,
            modality=modality,
            state=state,
            checklist_status={},
            slots={},
            plan=None,
            last_result=None,
            created_at=now,
            updated_at=now,
        )
        self._write(session, is_insert=True)
        return session

    def get(self, session_id: str) -> ConsultantSession | None:
        """Load a session by id, or ``None`` when absent."""
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM sessions WHERE session_id = ?", (session_id,)
            ).fetchone()
        return self._row_to_session(row) if row else None

    def latest_unfinished(self, project_id: str) -> ConsultantSession | None:
        """Return the most recently updated session not yet in ACCEPT state.

        Used on startup to resume an in-progress dialog (spec §4.1.1).
        """
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM sessions WHERE project_id = ? AND state != ? "
                "ORDER BY updated_at DESC LIMIT 1",
                (project_id, ConsultantState.ACCEPT.value),
            ).fetchone()
        return self._row_to_session(row) if row else None

    def save(self, session: ConsultantSession) -> ConsultantSession:
        """Persist the full session, bumping ``updated_at``."""
        session.updated_at = _now()
        self._write(session, is_insert=False)
        return session

    def _write(self, session: ConsultantSession, *, is_insert: bool) -> None:
        plan_json = session.plan.model_dump_json() if session.plan else None
        result_json = session.last_result.model_dump_json() if session.last_result else None
        payload = (
            session.session_id,
            session.project_id,
            session.modality.value if session.modality else None,
            session.state.value,
            json.dumps(session.checklist_status, ensure_ascii=False),
            json.dumps(session.slots, ensure_ascii=False),
            plan_json,
            result_json,
            session.created_at.isoformat(),
            session.updated_at.isoformat(),
        )
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO sessions "
                "(session_id, project_id, modality, state, checklist_status, slots, plan, last_result, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(session_id) DO UPDATE SET "
                "project_id=excluded.project_id, modality=excluded.modality, state=excluded.state, "
                "checklist_status=excluded.checklist_status, slots=excluded.slots, plan=excluded.plan, "
                "last_result=excluded.last_result, updated_at=excluded.updated_at",
                payload,
            )

    def _row_to_session(self, row: sqlite3.Row) -> ConsultantSession:
        modality_value = row["modality"]
        plan_raw = row["plan"]
        result_raw = row["last_result"]
        return ConsultantSession(
            session_id=row["session_id"],
            project_id=row["project_id"],
            modality=Modality(modality_value) if modality_value else None,
            state=ConsultantState(row["state"]),
            checklist_status=json.loads(row["checklist_status"] or "{}"),
            slots=json.loads(row["slots"] or "{}"),
            plan=ConsultantAnalysis.model_validate_json(plan_raw) if plan_raw else None,
            last_result=ClarifyResult.model_validate_json(result_raw) if result_raw else None,
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )
