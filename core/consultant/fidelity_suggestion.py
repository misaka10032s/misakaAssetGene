"""Fidelity-loop suggestion card builder (spec §5.15 / C-spec.md §4.3-4.4).

Mirrors ``core.consultant.planner``'s ``TrainingSuggestionCard`` pattern
(spec §4.4 / §5.12.1): a proposed action the frontend renders as a
clickable button; the consultant NEVER auto-executes it (spec §4.4 — "no
auto-exec").

Unlike ``TrainingSuggestionCard`` (keyed off the current session's
modality/prompt text, computed inside the stateless planner), this card's
emission condition is a PROJECT-level fact independent of what the current
message is about: does the project already have an IMAGE asset AND at
least one ``CharacterSheet`` with ``sheet_source_path`` set? Answering that
requires reading real project state (the assets list + the character-sheet
store), which the stateless planner has no access to — so this module is
kept pure (no I/O of its own); the caller (``core/main.py``) supplies the
already-loaded lists plus a small outfit-variant resolver callable (real
file I/O, injected so tests never touch the filesystem).
"""

from __future__ import annotations

from collections.abc import Callable

from core.models.schemas import AssetRecord, CharacterSheet, FidelitySuggestionCard, Modality

OutfitVariantResolver = Callable[[CharacterSheet], list[str]]


def select_fidelity_candidate(
    assets: list[AssetRecord],
    character_sheets: list[CharacterSheet],
) -> tuple[AssetRecord, CharacterSheet] | None:
    """Pick the (asset, character_sheet) pair a suggestion card should
    prefill, or ``None`` if the emission condition is not met.

    - IMAGE asset: ``modality is Modality.IMAGE`` AND ``asset_type == "image"``
      — excludes mask / refined-mask assets, which share ``modality=IMAGE``
      but a different ``asset_type`` (spec §5.15's checklist targets a
      portrait, not a mask). The MOST RECENTLY created one is used.
    - CharacterSheet: the FIRST one (creation order, i.e. list order — the
      asset store already returns them oldest-first) whose
      ``sheet_source_path`` is set — a wired-up real SSOT folder is the
      whole precondition for the fidelity loop to be runnable at all
      (spec §2.2).
    """
    image_assets = [asset for asset in assets if asset.modality is Modality.IMAGE and asset.asset_type == "image"]
    sheets_with_source = [sheet for sheet in character_sheets if sheet.sheet_source_path]
    if not image_assets or not sheets_with_source:
        return None
    newest_asset = max(image_assets, key=lambda item: item.created_at)
    return newest_asset, sheets_with_source[0]


def build_fidelity_suggestion_cards(
    assets: list[AssetRecord],
    character_sheets: list[CharacterSheet],
    *,
    outfit_variant_resolver: OutfitVariantResolver,
    auto_continue_default: bool,
) -> list[FidelitySuggestionCard]:
    """Return 0 or 1 :class:`FidelitySuggestionCard` for this project.

    At most one card — the frontend needs only one entry point per project;
    the user can pick a different asset/character/outfit inside the
    loop-start form itself before submitting (spec §5's
    ``FidelityLoopStartRequest`` already carries every field a UI would need
    for that).
    """
    candidate = select_fidelity_candidate(assets, character_sheets)
    if candidate is None:
        return []
    asset, sheet = candidate
    variants = outfit_variant_resolver(sheet)
    return [
        FidelitySuggestionCard(
            asset_id=asset.id,
            character_sheet_id=sheet.id,
            outfit_variant_choices=variants,
            auto_continue=auto_continue_default,
            reason=f"「{sheet.name}」已設定角色資料夾（sheet_source_path），可對這張立繪跑角色一致性自動精修迴圈。",
        )
    ]
