"""Engine-level tests: transitions, checklist gating, plan emission, restart.

These tests never touch a live LLM. The planner is deterministic static
analysis, and the engine is pointed at a temporary SQLite file.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from core.consultant.engine import ConsultantEngine
from core.models.schemas import ClarifyRequest, ConsultantState, Modality


def _engine(tmp_path: Path) -> ConsultantEngine:
    project_dir = tmp_path / "my_project"
    project_dir.mkdir(parents=True, exist_ok=True)
    return ConsultantEngine(sessions_path_resolver=lambda _project_id: project_dir)


def _complete_image_slots() -> dict[str, str]:
    return {"usage": "portrait", "resolution": "1024x1024", "style": "anime", "background": "transparent"}


def test_start_session_begins_in_clarify_when_checklist_incomplete(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    data = engine.start_session(
        "proj",
        ClarifyRequest(prompt="畫出角色立繪", modality=Modality.IMAGE),
    )
    assert data.session.state is ConsultantState.CLARIFY
    assert data.missing_slots  # checklist not yet filled
    assert data.result is not None


def test_clarify_loops_until_checklist_complete(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    started = engine.start_session(
        "proj",
        ClarifyRequest(prompt="畫出角色立繪", modality=Modality.IMAGE),
    )
    session_id = started.session.session_id

    # Partial fill -> still Clarify.
    step = engine.advance_session(
        "proj", session_id,
        ClarifyRequest(prompt="(continue)", modality=Modality.IMAGE),
        slots={"usage": "portrait"},
    )
    assert step.session.state is ConsultantState.CLARIFY
    assert step.missing_slots

    # Complete fill -> advances to Summary.
    step = engine.advance_session(
        "proj", session_id,
        ClarifyRequest(prompt="(continue)", modality=Modality.IMAGE),
        slots=_complete_image_slots(),
    )
    assert step.session.state is ConsultantState.SUMMARY
    assert step.missing_slots == []


def test_summary_to_generate_emits_structured_plan(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    started = engine.start_session(
        "proj",
        ClarifyRequest(prompt="生成「凱優香」的所有官方服裝立繪圖", modality=Modality.IMAGE),
    )
    session_id = started.session.session_id

    # Fill checklist to reach Summary.
    engine.advance_session(
        "proj", session_id,
        ClarifyRequest(prompt="(continue)", modality=Modality.IMAGE),
        slots=_complete_image_slots(),
    )
    # Summary -> Generate emits the execution plan (spec §5.12).
    generated = engine.advance_session(
        "proj", session_id,
        ClarifyRequest(prompt="(continue)", modality=Modality.IMAGE),
    )
    assert generated.session.state is ConsultantState.GENERATE
    assert generated.session.plan is not None
    assert len(generated.session.plan.execution_steps) > 0


def test_persistence_across_restart_new_engine_same_db(tmp_path: Path) -> None:
    project_dir = tmp_path / "my_project"
    project_dir.mkdir(parents=True, exist_ok=True)
    resolver = lambda _project_id: project_dir  # noqa: E731

    engine_a = ConsultantEngine(sessions_path_resolver=resolver)
    started = engine_a.start_session(
        "proj",
        ClarifyRequest(prompt="畫出角色立繪", modality=Modality.IMAGE),
    )
    session_id = started.session.session_id
    engine_a.advance_session(
        "proj", session_id,
        ClarifyRequest(prompt="(continue)", modality=Modality.IMAGE),
        slots={"usage": "portrait"},
    )

    # Simulate restart: brand new engine instance, same sqlite file.
    engine_b = ConsultantEngine(sessions_path_resolver=resolver)
    resumed = engine_b.resume_session("proj")
    assert resumed is not None
    assert resumed.session_id == session_id
    assert resumed.state is ConsultantState.CLARIFY
    assert resumed.slots["usage"] == "portrait"

    # Continuing from the resumed session works without re-stating the request.
    step = engine_b.advance_session(
        "proj", session_id,
        ClarifyRequest(prompt="(continue)", modality=Modality.IMAGE),
        slots=_complete_image_slots(),
    )
    assert step.session.state is ConsultantState.SUMMARY


def test_advance_unknown_session_raises(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    with pytest.raises(KeyError):
        engine.advance_session(
            "proj", "nope",
            ClarifyRequest(prompt="(continue)", modality=Modality.IMAGE),
        )


def test_legacy_clarify_still_works(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    result = engine.clarify(ClarifyRequest(prompt="生成 BGM", modality=Modality.MUSIC))
    assert result.modality is Modality.MUSIC
    assert result.analysis is not None
