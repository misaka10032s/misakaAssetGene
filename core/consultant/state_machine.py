"""Explicit consultant dialog state machine (spec §4.1).

Models the Intake -> Clarify -> Summary -> Generate -> Refine -> Accept flow
as deterministic transitions. The engine drives transitions; this module only
encodes which transitions are legal and the checklist gating that keeps the
dialog in Clarify until the required slots are filled.
"""

from __future__ import annotations

from core.models.schemas import ConsultantState, Modality, RefineStrategy

# Required checklist slot keys per modality. These mirror the hard-coded domain
# checklist from spec §4.2 and the question prompts in ``checklists.py``. Each
# key is a stable, language-neutral slot identifier the frontend / engine fills.
REQUIRED_SLOTS: dict[str, list[str]] = {
    Modality.MUSIC.value: ["usage", "mood", "tempo", "instruments", "length"],
    Modality.IMAGE.value: ["usage", "resolution", "style", "background"],
    Modality.VOICE.value: ["persona", "tone", "language", "lipsync"],
    Modality.VIDEO.value: ["usage", "duration", "audio_track", "camera"],
    Modality.TEXT.value: ["objective"],
}

# Legal forward transitions. Refine can loop back to Generate; any state can be
# abandoned back to Intake (handled separately as a reset).
_ALLOWED_TRANSITIONS: dict[ConsultantState, set[ConsultantState]] = {
    ConsultantState.INTAKE: {ConsultantState.CLARIFY, ConsultantState.SUMMARY},
    ConsultantState.CLARIFY: {ConsultantState.CLARIFY, ConsultantState.SUMMARY},
    ConsultantState.SUMMARY: {ConsultantState.GENERATE, ConsultantState.CLARIFY},
    ConsultantState.GENERATE: {ConsultantState.REFINE, ConsultantState.ACCEPT},
    ConsultantState.REFINE: {ConsultantState.REFINE, ConsultantState.GENERATE, ConsultantState.ACCEPT},
    ConsultantState.ACCEPT: set(),
}


def required_slots_for(modality: Modality | None) -> list[str]:
    """Return the required checklist slot keys for a modality."""
    key = modality.value if modality else Modality.TEXT.value
    return list(REQUIRED_SLOTS.get(key, REQUIRED_SLOTS[Modality.TEXT.value]))


def missing_slots(modality: Modality | None, slots: dict[str, object]) -> list[str]:
    """Return required slots that are not yet satisfied.

    A slot counts as filled when present with a truthy, non-blank value.
    """
    result: list[str] = []
    for slot in required_slots_for(modality):
        value = slots.get(slot)
        if value is None or (isinstance(value, str) and not value.strip()):
            result.append(slot)
    return result


def checklist_status(modality: Modality | None, slots: dict[str, object]) -> dict[str, bool]:
    """Return a per-slot filled/unfilled map for the modality checklist."""
    unfilled = set(missing_slots(modality, slots))
    return {slot: slot not in unfilled for slot in required_slots_for(modality)}


def is_checklist_complete(modality: Modality | None, slots: dict[str, object]) -> bool:
    """True when every required slot for the modality is filled."""
    return not missing_slots(modality, slots)


def can_transition(current: ConsultantState, target: ConsultantState) -> bool:
    """Return whether moving from ``current`` to ``target`` is legal."""
    if target is ConsultantState.INTAKE:
        # Reset is always allowed (abandon current dialog).
        return True
    return target in _ALLOWED_TRANSITIONS.get(current, set())


class InvalidTransitionError(ValueError):
    """Raised when an illegal state transition is attempted."""


def assert_transition(current: ConsultantState, target: ConsultantState) -> None:
    """Raise :class:`InvalidTransitionError` if the transition is illegal."""
    if not can_transition(current, target):
        raise InvalidTransitionError(f"Illegal consultant transition: {current.value} -> {target.value}")


def refine_target_state(strategy: RefineStrategy, *, accept: bool = False) -> ConsultantState:
    """Map a §6.2 refine strategy onto the §4.1 dialog state transition.

    The Generate -> Refine -> (Generate | Accept) loop is driven by the refine
    strategy chosen for the request:

    - An explicit ``accept`` always ends the loop at Accept.
    - ``metadata_only`` does not re-render, so the dialog stays in Refine
      awaiting the next instruction.
    - Every render-bearing strategy (param retune / img2img / inpaint /
      full regen) re-enters Generate to run the produced refine job.
    """
    if accept:
        return ConsultantState.ACCEPT
    if strategy is RefineStrategy.METADATA_ONLY:
        return ConsultantState.REFINE
    return ConsultantState.GENERATE


def next_state(
    current: ConsultantState,
    modality: Modality | None,
    slots: dict[str, object],
    *,
    accept: bool = False,
) -> ConsultantState:
    """Compute the next state given the current state and checklist gating.

    - Intake / Clarify stay in Clarify until the checklist is complete, then
      advance to Summary.
    - Summary advances to Generate (planning is emitted on this edge).
    - Generate advances to Refine; an explicit accept jumps to Accept.
    - Refine loops on itself unless the user accepts.
    """
    if current in (ConsultantState.INTAKE, ConsultantState.CLARIFY):
        return ConsultantState.SUMMARY if is_checklist_complete(modality, slots) else ConsultantState.CLARIFY
    if current is ConsultantState.SUMMARY:
        return ConsultantState.GENERATE
    if current is ConsultantState.GENERATE:
        return ConsultantState.ACCEPT if accept else ConsultantState.REFINE
    if current is ConsultantState.REFINE:
        return ConsultantState.ACCEPT if accept else ConsultantState.REFINE
    return current
