"""Consultant engine: dialog state machine + session persistence (spec §4.1).

The engine is no longer a stateless per-call helper. Each session-aware call
loads the session from SQLite (via :class:`SessionStore`), applies a state
machine transition, persists the result, and returns the updated session. The
legacy stateless :meth:`clarify` is retained for backward compatibility.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from pathlib import Path

from core.consultant.few_shot import load_prompt_template
from core.consultant.planner import build_clarify_result
from core.consultant.session_store import SessionStore
from core.consultant import state_machine
from core.models.schemas import (
    ClarifyRequest,
    ClarifyResult,
    ConsultantSession,
    ConsultantSessionData,
    ConsultantState,
    Modality,
)

# Resolves a project id to the directory that holds its ``memory.sqlite``.
SessionsPathResolver = Callable[[str], Path]


class ConsultantEngine:
    def __init__(self, sessions_path_resolver: SessionsPathResolver | None = None) -> None:
        self.prompt_dir = Path(__file__).resolve().parent / "prompts"
        self._resolve_project_dir = sessions_path_resolver
        self._stores: dict[str, SessionStore] = {}

    # ------------------------------------------------------------------
    # Legacy stateless entry point (kept for backward compatibility).
    # ------------------------------------------------------------------
    def clarify(self, request: ClarifyRequest) -> ClarifyResult:
        modality = request.modality.value if request.modality else "text"
        template = load_prompt_template(self.prompt_dir, modality)
        return build_clarify_result(request, bool(template))

    # ------------------------------------------------------------------
    # Session-aware state machine entry points.
    # ------------------------------------------------------------------
    def store_for(self, project_id: str) -> SessionStore:
        """Return (and cache) the SQLite session store for a project."""
        if self._resolve_project_dir is None:
            raise RuntimeError("ConsultantEngine has no sessions path resolver configured.")
        if project_id not in self._stores:
            project_dir = self._resolve_project_dir(project_id)
            self._stores[project_id] = SessionStore(project_dir / "memory.sqlite")
        return self._stores[project_id]

    def resume_session(self, project_id: str) -> ConsultantSession | None:
        """Return the latest unfinished session for a project, if any (spec §4.1.1)."""
        return self.store_for(project_id).latest_unfinished(project_id)

    def start_session(
        self,
        project_id: str,
        request: ClarifyRequest,
        *,
        session_id: str | None = None,
    ) -> ConsultantSessionData:
        """Create or resume a session and run the Intake/Clarify analysis.

        If ``session_id`` is provided and exists, the session is resumed;
        otherwise a fresh session is created in the Intake state.
        """
        store = self.store_for(project_id)
        session = store.get(session_id) if session_id else None
        if session is None:
            session = store.create(
                session_id=session_id or uuid.uuid4().hex,
                project_id=project_id,
                modality=request.modality,
            )
        return self._run_clarify(store, session, request, slots={}, accept=False)

    def advance_session(
        self,
        project_id: str,
        session_id: str,
        request: ClarifyRequest,
        *,
        slots: dict[str, object] | None = None,
        accept: bool = False,
    ) -> ConsultantSessionData:
        """Load a session, apply the next transition, and persist it."""
        store = self.store_for(project_id)
        session = store.get(session_id)
        if session is None:
            raise KeyError(f"Consultant session not found: {session_id}")
        return self._run_clarify(store, session, request, slots=slots or {}, accept=accept)

    # ------------------------------------------------------------------
    # Internal transition driver.
    # ------------------------------------------------------------------
    def _run_clarify(
        self,
        store: SessionStore,
        session: ConsultantSession,
        request: ClarifyRequest,
        *,
        slots: dict[str, object],
        accept: bool,
    ) -> ConsultantSessionData:
        # Merge newly supplied slots into the session record, whitelisting only
        # keys defined in the modality's required checklist. Unknown keys are
        # logged and discarded to prevent uncontrolled state growth.
        if slots:
            effective_mod = request.modality or session.modality
            allowed_keys = set(state_machine.required_slots_for(effective_mod))
            unknown = {k: v for k, v in slots.items() if k not in allowed_keys}
            if unknown:
                import logging
                logging.getLogger(__name__).warning(
                    "advance_session: ignoring unknown slot keys %s for modality=%s",
                    list(unknown.keys()),
                    effective_mod.value if effective_mod else "unknown",
                )
            session.slots = {**session.slots, **{k: v for k, v in slots.items() if k in allowed_keys}}

        modality = request.modality or session.modality
        # Run the planner so we always have a fresh analysis to reason over.
        template = load_prompt_template(self.prompt_dir, (modality.value if modality else "text"))
        result = build_clarify_result(request, bool(template))
        effective_modality = modality or result.modality
        session.modality = effective_modality
        session.last_result = result

        # Refresh checklist gating based on the (possibly updated) slots.
        session.checklist_status = state_machine.checklist_status(effective_modality, session.slots)

        target = state_machine.next_state(
            session.state,
            effective_modality,
            session.slots,
            accept=accept,
        )
        state_machine.assert_transition(session.state, target)
        session.state = target

        # On the Summary -> Generate edge, emit the structured execution plan
        # (spec §5.12) and persist it on the session.
        missing = state_machine.missing_slots(effective_modality, session.slots)
        if target is ConsultantState.GENERATE and result.analysis is not None:
            session.plan = result.analysis

        store.save(session)
        return ConsultantSessionData(session=session, result=result, missing_slots=missing)
