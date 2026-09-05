"""Tests for core/consultant/fidelity.py — checklist derivation (spec §5.15 / §2.1).

Fixtures below are 100% SYNTHETIC — an invented test character ("測試花") in
the SAME markdown structure as the real per-character SSOT files
(``setting.md`` + ``outfits.md``), but none of the real character's text is
reproduced anywhere here (hard rule: never copy real character files into
``tests/``).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from core.consultant.fidelity import (
    list_outfit_variants,
    load_character_sources,
    parse_character_checklist,
)
from core.models.schemas import BodyRegion

SETTING_MD = """# 角色設定：測試花

## 📋 基礎資料 (Basic Profile)
- **姓名**：測試花
- **年齡**：99 歲

## 🎨 外型特徵 (Visual Identity)
- **髮型**：銀色長直髮，帶有側邊瀏海。
- **瞳色**：
    - 平常：淡紫色瞳孔。
    - 特殊：發光時轉為金色。
- **配件**：方框眼鏡。
- **服裝原則**：不凸顯胸部，偏向樸素。

## 🏷️ 標籤 (Tags for Generation)
`test hana, 1girl, solo, silver hair, long hair, side bangs, square eyewear, purple eyes`

## 🖼️ 圖像生成提示詞 (Image Generation Prompts)
- **基礎形象 (Base Character)**:
    - **ComfyUI**: `best quality, test hana, silver hair, long hair, square eyewear, purple eyes`
"""

OUTFITS_MD = """# 服裝與形態變體：測試花

## 👗 常駐服裝 (Static Outfits)
1. **[TestA] 測試服裝甲 (Test Outfit A)**:
    - **整體描述**：簡單的測試連身裙。
    - **頭部**：測試髮夾。
    - **臉部**：保留方框眼鏡。
    - **身體服裝**：白色連身裙。
        - **細節**：胸前有藍色蝴蝶結。
    - **飾品**：腰間細繩。
    - **裙子/下部**：及膝裙擺。
    - **鞋子**：白色便鞋。
    - **襪子**：白色短襪。
    - **生成提示詞**：
        - **ComfyUI**: `test hana, 1girl, solo, silver hair, white dress, blue ribbon on chest, waist string, white shoes, white socks, test hairpin`
        - **Gemini/GPT**: `A test character description in prose.`

2. **[TestB] 測試服裝乙 (Test Outfit B)**:
    - **整體描述**：另一套沒有生成提示詞區塊的測試服裝。
    - **頭部**：測試帽子。
    - **裙子/下部**：長裙擺。

## 🎭 特殊形態 (Special Forms)
1. **[TestC] 無巢狀子項的形態**:
    - 這一項只有一行敘述，沒有巢狀 bullet。
"""


class TestListOutfitVariants:
    def test_returns_all_bracket_tags_in_order(self) -> None:
        variants = list_outfit_variants(OUTFITS_MD)
        assert variants == ["testa", "testb", "testc"]

    def test_slugified_lowercase(self) -> None:
        variants = list_outfit_variants(OUTFITS_MD)
        assert all(v == v.lower() for v in variants)


class TestParseSettingChecks:
    def test_setting_bullets_become_checks(self) -> None:
        checks = parse_character_checklist(SETTING_MD, OUTFITS_MD, "TestA")
        setting_checks = [c for c in checks if c.source == "setting"]
        # 髮型, 瞳色, 配件, 服裝原則 — four top-level bullets under 外型特徵.
        assert len(setting_checks) == 4
        labels = [c.label_zh for c in setting_checks]
        assert labels == ["髮型", "瞳色", "配件", "服裝原則"]

    def test_pass_criteria_is_verbatim_including_nested_lines(self) -> None:
        checks = parse_character_checklist(SETTING_MD, OUTFITS_MD, "TestA")
        eye_check = next(c for c in checks if c.label_zh == "瞳色")
        assert "淡紫色瞳孔" in eye_check.pass_criteria
        assert "發光時轉為金色" in eye_check.pass_criteria

    def test_region_hint_mapping(self) -> None:
        checks = {c.label_zh: c for c in parse_character_checklist(SETTING_MD, OUTFITS_MD, "TestA")}
        assert checks["髮型"].region_hint == BodyRegion.HEAD
        assert checks["瞳色"].region_hint == BodyRegion.FACE
        assert checks["配件"].region_hint == BodyRegion.FACE
        assert checks["服裝原則"].region_hint == BodyRegion.TORSO

    def test_fix_tags_from_tag_line_substring_match(self) -> None:
        checks = {c.label_zh: c for c in parse_character_checklist(SETTING_MD, OUTFITS_MD, "TestA")}
        assert checks["髮型"].fix_tags == ["silver hair", "long hair", "side bangs"]
        assert checks["瞳色"].fix_tags == ["purple eyes"]
        assert checks["配件"].fix_tags == ["square eyewear"]

    def test_fix_tags_fallback_to_whole_line_when_no_keyword_match(self) -> None:
        # "服裝原則" keywords (petite/cleavage/bust) match nothing in the tag
        # line, so it must fall back to the ENTIRE tag pool, not an empty list.
        checks = {c.label_zh: c for c in parse_character_checklist(SETTING_MD, OUTFITS_MD, "TestA")}
        principle_check = checks["服裝原則"]
        assert len(principle_check.fix_tags) == 8
        assert "test hana" in principle_check.fix_tags


class TestParseOutfitChecks:
    def test_outfit_bullets_become_checks_excluding_prompt_bullet(self) -> None:
        checks = parse_character_checklist(SETTING_MD, OUTFITS_MD, "TestA")
        outfit_checks = [c for c in checks if c.source == "outfits"]
        labels = [c.label_zh for c in outfit_checks]
        assert "生成提示詞" not in labels
        assert labels == ["整體描述", "頭部", "臉部", "身體服裝", "飾品", "裙子/下部", "鞋子", "襪子"]

    def test_outfit_pass_criteria_includes_nested_detail(self) -> None:
        checks = parse_character_checklist(SETTING_MD, OUTFITS_MD, "TestA")
        body_check = next(c for c in checks if c.source == "outfits" and c.label_zh == "身體服裝")
        assert "白色連身裙" in body_check.pass_criteria
        assert "胸前有藍色蝴蝶結" in body_check.pass_criteria

    def test_outfit_fix_tags_from_comfyui_line(self) -> None:
        checks = {
            c.label_zh: c for c in parse_character_checklist(SETTING_MD, OUTFITS_MD, "TestA")
            if c.source == "outfits"
        }
        assert checks["鞋子"].fix_tags == ["white shoes"]
        assert checks["襪子"].fix_tags == ["white socks"]
        # "ribbon" is a HEAD keyword (hair ribbons are common), so it also
        # matches "blue ribbon on chest" here even though this particular
        # ribbon is torso-located — a documented heuristic false-positive
        # (module docstring), not something this parser can disambiguate
        # from tag text alone.
        assert checks["頭部"].fix_tags == ["silver hair", "blue ribbon on chest", "test hairpin"]

    def test_outfit_fix_tags_fallback_to_whole_comfyui_line(self) -> None:
        # This fixture's ComfyUI line has no "skirt"/"pleat" tag at all, so
        # "裙子/下部" must fall back to the ENTIRE ComfyUI tag pool
        # (spec §2.1 "找不到就整段 fallback"), not an empty list.
        checks = {
            c.label_zh: c for c in parse_character_checklist(SETTING_MD, OUTFITS_MD, "TestA")
            if c.source == "outfits"
        }
        assert checks["裙子/下部"].fix_tags == [
            "test hana", "1girl", "solo", "silver hair", "white dress",
            "blue ribbon on chest", "waist string", "white shoes",
            "white socks", "test hairpin",
        ]

    def test_outfit_region_hints(self) -> None:
        checks = {
            c.label_zh: c for c in parse_character_checklist(SETTING_MD, OUTFITS_MD, "TestA")
            if c.source == "outfits"
        }
        assert checks["頭部"].region_hint == BodyRegion.HEAD
        assert checks["臉部"].region_hint == BodyRegion.FACE
        assert checks["身體服裝"].region_hint == BodyRegion.TORSO
        assert checks["飾品"].region_hint == BodyRegion.WAIST
        assert checks["裙子/下部"].region_hint == BodyRegion.LEGS
        assert checks["鞋子"].region_hint == BodyRegion.LEGS
        assert checks["襪子"].region_hint == BodyRegion.LEGS

    def test_outfit_without_generation_prompt_block_still_parses(self) -> None:
        checks = parse_character_checklist(SETTING_MD, OUTFITS_MD, "TestB")
        outfit_checks = [c for c in checks if c.source == "outfits"]
        labels = [c.label_zh for c in outfit_checks]
        assert labels == ["整體描述", "頭部", "裙子/下部"]
        # No ComfyUI line found -> empty tag pool -> fix_tags is empty, not a crash.
        assert all(c.fix_tags == [] for c in outfit_checks)

    def test_outfit_with_no_nested_bullets_yields_zero_outfit_checks_not_error(self) -> None:
        # TestC's item body is a single plain description line with no
        # "- **label**：" bullets — a legitimate (if checklist-poor) outfit,
        # not a parse error.
        checks = parse_character_checklist(SETTING_MD, OUTFITS_MD, "TestC")
        outfit_checks = [c for c in checks if c.source == "outfits"]
        assert outfit_checks == []
        setting_checks = [c for c in checks if c.source == "setting"]
        assert len(setting_checks) == 4


class TestUnknownVariant:
    def test_unknown_variant_raises_with_available_list(self) -> None:
        with pytest.raises(ValueError) as exc_info:
            parse_character_checklist(SETTING_MD, OUTFITS_MD, "NoSuchVariant")
        message = str(exc_info.value)
        assert "NoSuchVariant" in message
        assert "testa" in message
        assert "testb" in message
        assert "testc" in message

    def test_variant_match_is_case_insensitive(self) -> None:
        checks_lower = parse_character_checklist(SETTING_MD, OUTFITS_MD, "testa")
        checks_mixed = parse_character_checklist(SETTING_MD, OUTFITS_MD, "TeStA")
        assert len(checks_lower) == len(checks_mixed) == 12


class TestMissingSettingSection:
    def test_missing_visual_identity_section_raises(self) -> None:
        broken_setting = "# 角色設定：無外型段落\n\n## 📋 基礎資料\n- **姓名**：無\n"
        with pytest.raises(ValueError, match="外型特徵"):
            parse_character_checklist(broken_setting, OUTFITS_MD, "TestA")


class TestLoadCharacterSources:
    def test_reads_only_setting_and_outfits(self, tmp_path: Path) -> None:
        (tmp_path / "setting.md").write_text(SETTING_MD, encoding="utf-8")
        (tmp_path / "outfits.md").write_text(OUTFITS_MD, encoding="utf-8")
        # A decoy file that must NEVER be read by load_character_sources.
        (tmp_path / "notes.md").write_text("SECRET_MARKER_DO_NOT_LEAK", encoding="utf-8")

        setting_text, outfits_text = load_character_sources(str(tmp_path))

        assert setting_text == SETTING_MD
        assert outfits_text == OUTFITS_MD
        assert "SECRET_MARKER_DO_NOT_LEAK" not in setting_text
        assert "SECRET_MARKER_DO_NOT_LEAK" not in outfits_text

    def test_refuses_non_directory(self, tmp_path: Path) -> None:
        not_a_dir = tmp_path / "not-a-folder.txt"
        not_a_dir.write_text("hello", encoding="utf-8")
        with pytest.raises(ValueError, match="not a directory"):
            load_character_sources(str(not_a_dir))

    def test_refuses_missing_directory(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="not a directory"):
            load_character_sources(str(tmp_path / "does-not-exist"))

    def test_missing_setting_md_raises(self, tmp_path: Path) -> None:
        (tmp_path / "outfits.md").write_text(OUTFITS_MD, encoding="utf-8")
        with pytest.raises(FileNotFoundError, match=r"setting\.md"):
            load_character_sources(str(tmp_path))

    def test_missing_outfits_md_raises(self, tmp_path: Path) -> None:
        (tmp_path / "setting.md").write_text(SETTING_MD, encoding="utf-8")
        with pytest.raises(FileNotFoundError, match=r"outfits\.md"):
            load_character_sources(str(tmp_path))
