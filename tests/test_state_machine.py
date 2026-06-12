"""Unit tests for the consultant dialog state machine (spec §4.1)."""

from __future__ import annotations

import pytest

from core.consultant import state_machine
from core.consultant.state_machine import InvalidTransitionError
from core.models.schemas import ConsultantState, Modality


def test_required_slots_per_modality() -> None:
    assert "tempo" in state_machine.required_slots_for(Modality.MUSIC)
    assert "resolution" in state_machine.required_slots_for(Modality.IMAGE)
    # Unknown / None falls back to the text checklist.
    assert state_machine.required_slots_for(None) == state_machine.required_slots_for(Modality.TEXT)


def test_missing_slots_detects_unfilled_and_blank() -> None:
    slots = {"usage": "battle scene", "mood": "  ", "tempo": None}
    missing = state_machine.missing_slots(Modality.MUSIC, slots)
    assert "usage" not in missing
    assert "mood" in missing  # blank string is unfilled
    assert "tempo" in missing  # None is unfilled
    assert "instruments" in missing  # absent key


def test_checklist_complete_when_all_filled() -> None:
    slots = {key: "ok" for key in state_machine.required_slots_for(Modality.IMAGE)}
    assert state_machine.is_checklist_complete(Modality.IMAGE, slots) is True
    assert state_machine.missing_slots(Modality.IMAGE, slots) == []


def test_clarify_gating_stays_until_checklist_complete() -> None:
    # Incomplete -> stays in Clarify.
    incomplete = {"usage": "icon"}
    assert (
        state_machine.next_state(ConsultantState.CLARIFY, Modality.IMAGE, incomplete)
        is ConsultantState.CLARIFY
    )
    # Complete -> advances to Summary.
    complete = {key: "ok" for key in state_machine.required_slots_for(Modality.IMAGE)}
    assert (
        state_machine.next_state(ConsultantState.CLARIFY, Modality.IMAGE, complete)
        is ConsultantState.SUMMARY
    )


def test_full_forward_flow() -> None:
    complete = {key: "ok" for key in state_machine.required_slots_for(Modality.MUSIC)}
    assert state_machine.next_state(ConsultantState.INTAKE, Modality.MUSIC, complete) is ConsultantState.SUMMARY
    assert state_machine.next_state(ConsultantState.SUMMARY, Modality.MUSIC, complete) is ConsultantState.GENERATE
    assert state_machine.next_state(ConsultantState.GENERATE, Modality.MUSIC, complete) is ConsultantState.REFINE
    assert (
        state_machine.next_state(ConsultantState.GENERATE, Modality.MUSIC, complete, accept=True)
        is ConsultantState.ACCEPT
    )
    assert state_machine.next_state(ConsultantState.REFINE, Modality.MUSIC, complete) is ConsultantState.REFINE
    assert (
        state_machine.next_state(ConsultantState.REFINE, Modality.MUSIC, complete, accept=True)
        is ConsultantState.ACCEPT
    )


def test_illegal_transition_raises() -> None:
    with pytest.raises(InvalidTransitionError):
        state_machine.assert_transition(ConsultantState.INTAKE, ConsultantState.GENERATE)
    # Reset to Intake is always allowed.
    state_machine.assert_transition(ConsultantState.GENERATE, ConsultantState.INTAKE)
