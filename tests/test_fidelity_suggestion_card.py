"""Tests for core/consultant/fidelity_suggestion.py (spec §5.15 / C-spec.md
§4.3-4.4) — FidelitySuggestionCard emission conditions.

Pure logic only: ``outfit_variant_resolver`` is always a fake callable here
(no filesystem access), matching this module's own design (it takes the
resolver injected, never reads files itself).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from core.consultant.fidelity_suggestion import (
    build_fidelity_suggestion_cards,
    select_fidelity_candidate,
)
from core.models.schemas import AssetRecord, CharacterSheet, Modality

_NOW = datetime(2026, 9, 6, tzinfo=UTC)


def _asset(id: str, *, asset_type: str = "image", modality: Modality = Modality.IMAGE, created_at: datetime = _NOW) -> AssetRecord:
    return AssetRecord(
        id=id,
        modality=modality,
        asset_type=asset_type,
        title=id,
        path=f"assets/{id}.png",
        created_at=created_at,
    )


def _sheet(id: str, *, sheet_source_path: str | None = None, name: str = "測試花") -> CharacterSheet:
    return CharacterSheet(
        id=id,
        project_id="proj-1",
        name=name,
        sheet_source_path=sheet_source_path,
        created_at=_NOW,
        updated_at=_NOW,
    )


class TestSelectFidelityCandidate:
    def test_no_assets_no_sheets_returns_none(self) -> None:
        assert select_fidelity_candidate([], []) is None

    def test_image_asset_but_no_sheet_with_source_path_returns_none(self) -> None:
        assets = [_asset("a1")]
        sheets = [_sheet("s1", sheet_source_path=None)]
        assert select_fidelity_candidate(assets, sheets) is None

    def test_sheet_with_source_path_but_no_image_asset_returns_none(self) -> None:
        assets: list[AssetRecord] = []
        sheets = [_sheet("s1", sheet_source_path="/tmp/char")]
        assert select_fidelity_candidate(assets, sheets) is None

    def test_only_mask_asset_present_does_not_count_as_image(self) -> None:
        """A mask asset shares modality=IMAGE but asset_type='mask' — must
        not be mistaken for a root portrait (spec §5.15 checklist targets a
        portrait, not a mask)."""
        assets = [_asset("m1", asset_type="mask")]
        sheets = [_sheet("s1", sheet_source_path="/tmp/char")]
        assert select_fidelity_candidate(assets, sheets) is None

    def test_image_asset_and_sheet_with_source_path_returns_pair(self) -> None:
        assets = [_asset("a1")]
        sheets = [_sheet("s1", sheet_source_path="/tmp/char")]
        result = select_fidelity_candidate(assets, sheets)
        assert result is not None
        asset, sheet = result
        assert asset.id == "a1"
        assert sheet.id == "s1"

    def test_picks_most_recently_created_image_asset(self) -> None:
        older = _asset("old", created_at=_NOW - timedelta(days=1))
        newer = _asset("new", created_at=_NOW)
        sheets = [_sheet("s1", sheet_source_path="/tmp/char")]
        result = select_fidelity_candidate([older, newer], sheets)
        assert result is not None
        asset, _sheet_obj = result
        assert asset.id == "new"

    def test_picks_first_sheet_with_source_path_in_list_order(self) -> None:
        assets = [_asset("a1")]
        sheets = [
            _sheet("no-source", sheet_source_path=None),
            _sheet("has-source-first", sheet_source_path="/tmp/char-a"),
            _sheet("has-source-second", sheet_source_path="/tmp/char-b"),
        ]
        result = select_fidelity_candidate(assets, sheets)
        assert result is not None
        _asset_obj, sheet = result
        assert sheet.id == "has-source-first"


class TestBuildFidelitySuggestionCards:
    def test_no_condition_met_returns_empty_list(self) -> None:
        cards = build_fidelity_suggestion_cards(
            [], [], outfit_variant_resolver=lambda sheet: [], auto_continue_default=False
        )
        assert cards == []

    def test_condition_met_returns_one_card_with_expected_fields(self) -> None:
        assets = [_asset("root-asset")]
        sheets = [_sheet("sheet-1", sheet_source_path="/tmp/char")]
        resolver_calls: list[str] = []

        def resolver(sheet: CharacterSheet) -> list[str]:
            resolver_calls.append(sheet.id)
            return ["default", "gothic"]

        cards = build_fidelity_suggestion_cards(
            assets, sheets, outfit_variant_resolver=resolver, auto_continue_default=True
        )
        assert len(cards) == 1
        card = cards[0]
        assert card.action == "start_fidelity_loop"
        assert card.asset_id == "root-asset"
        assert card.character_sheet_id == "sheet-1"
        assert card.outfit_variant_choices == ["default", "gothic"]
        assert card.auto_continue is True
        assert card.reason  # non-empty, names the character
        assert resolver_calls == ["sheet-1"]

    def test_missing_image_asset_returns_empty_list(self) -> None:
        sheets = [_sheet("sheet-1", sheet_source_path="/tmp/char")]
        cards = build_fidelity_suggestion_cards(
            [], sheets, outfit_variant_resolver=lambda sheet: [], auto_continue_default=False
        )
        assert cards == []

    def test_missing_sheet_source_path_returns_empty_list(self) -> None:
        assets = [_asset("root-asset")]
        sheets = [_sheet("sheet-1", sheet_source_path=None)]
        cards = build_fidelity_suggestion_cards(
            assets, sheets, outfit_variant_resolver=lambda sheet: [], auto_continue_default=False
        )
        assert cards == []

    def test_auto_continue_default_false_propagated(self) -> None:
        assets = [_asset("root-asset")]
        sheets = [_sheet("sheet-1", sheet_source_path="/tmp/char")]
        cards = build_fidelity_suggestion_cards(
            assets, sheets, outfit_variant_resolver=lambda sheet: [], auto_continue_default=False
        )
        assert cards[0].auto_continue is False
