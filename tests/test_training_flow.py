"""Tests for M4.b training-flow consultant extension (spec §7.1.1).

Coverage:
  - Training-intent detection (keyword-based heuristic in planner)
  - TRAINING modality checklist: four required slots (character_sheet /
    dataset_pack / training_recipe / lora_preset), optional i2v_recipe
  - Checklist progression through the training-flow state machine
  - Plan output referencing entity IDs in slots (training_character_sheet_id …)
  - Suggestion-card emission: all four required cards + optional i2v card
  - Suggestion-card schema correctness (entity_kind, action, prefilled, reason)
  - state_machine.REQUIRED_SLOTS["training"] completeness
  - Engine restart / persistence for training sessions
  - Training summary & next_step text sanity
"""

from __future__ import annotations

from pathlib import Path

import pytest

from core.consultant.checklists import CHECKLISTS
from core.consultant.engine import ConsultantEngine
from core.consultant import state_machine
from core.consultant.planner import _is_training_intent, build_clarify_result
from core.models.schemas import (
    ClarifyRequest,
    ConsultantState,
    Modality,
    TrainingSuggestionCard,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _engine(tmp_path: Path) -> ConsultantEngine:
    project_dir = tmp_path / "my_project"
    project_dir.mkdir(parents=True, exist_ok=True)
    return ConsultantEngine(sessions_path_resolver=lambda _pid: project_dir)


def _complete_training_slots() -> dict[str, str]:
    """Minimal slot set that satisfies all four REQUIRED_SLOTS for TRAINING."""
    return {
        "character_sheet": "cs-id-001",
        "dataset_pack": "dp-id-001",
        "training_recipe": "tr-id-001",
        "lora_preset": "lp-id-001",
    }


# ---------------------------------------------------------------------------
# Training-intent detection
# ---------------------------------------------------------------------------

class TestTrainingIntentDetection:
    def test_lora_keyword_zh(self) -> None:
        assert _is_training_intent("我想訓練一個 LoRA")

    def test_lora_keyword_en(self) -> None:
        assert _is_training_intent("train a lora for my character")

    def test_train_keyword_zh(self) -> None:
        assert _is_training_intent("訓練角色模型")

    def test_dataset_keyword(self) -> None:
        assert _is_training_intent("準備 dataset 開始訓練")

    def test_kohya_keyword(self) -> None:
        assert _is_training_intent("使用 kohya 訓練")

    def test_trigger_word_keyword(self) -> None:
        assert _is_training_intent("設定觸發詞 trigger word")

    def test_gpt_sovits_keyword(self) -> None:
        assert _is_training_intent("用 gpt-sovits 複製聲線")

    def test_voice_clone_keyword_en(self) -> None:
        assert _is_training_intent("voice clone training")

    def test_character_factory_keyword(self) -> None:
        assert _is_training_intent("建立角色工廠")

    def test_non_training_image_prompt(self) -> None:
        assert not _is_training_intent("畫出角色立繪")

    def test_non_training_music_prompt(self) -> None:
        assert not _is_training_intent("生成 BGM 背景音樂")

    def test_empty_prompt(self) -> None:
        assert not _is_training_intent("")

    def test_modality_inferred_as_training(self) -> None:
        """When prompt triggers training-intent detection, modality must be TRAINING."""
        result = build_clarify_result(
            ClarifyRequest(prompt="訓練 LoRA 角色模型", modality=None),
            template_loaded=False,
        )
        assert result.modality is Modality.TRAINING

    def test_explicit_training_modality_bypasses_detection(self) -> None:
        """Explicit TRAINING modality must be respected even without keywords."""
        result = build_clarify_result(
            ClarifyRequest(prompt="我想做一些東西", modality=Modality.TRAINING),
            template_loaded=False,
        )
        assert result.modality is Modality.TRAINING


# ---------------------------------------------------------------------------
# REQUIRED_SLOTS for TRAINING
# ---------------------------------------------------------------------------

class TestTrainingRequiredSlots:
    def test_training_has_four_required_slots(self) -> None:
        slots = state_machine.REQUIRED_SLOTS[Modality.TRAINING.value]
        assert len(slots) == 4, f"Expected 4 required slots, got {len(slots)}: {slots}"

    def test_training_slot_keys_match_spec(self) -> None:
        slots = state_machine.REQUIRED_SLOTS[Modality.TRAINING.value]
        required = {"character_sheet", "dataset_pack", "training_recipe", "lora_preset"}
        assert set(slots) == required

    def test_i2v_recipe_is_not_required(self) -> None:
        """i2v_recipe is optional (spec §7.1.1 step e); it must not block checklist completion."""
        slots = state_machine.REQUIRED_SLOTS[Modality.TRAINING.value]
        assert "i2v_recipe" not in slots

    def test_training_checklist_questions_exist(self) -> None:
        assert "training" in CHECKLISTS
        assert len(CHECKLISTS["training"]) >= 4


# ---------------------------------------------------------------------------
# Checklist progression through the training-flow state machine
# ---------------------------------------------------------------------------

class TestTrainingChecklistProgression:
    def test_start_session_begins_in_clarify_when_slots_empty(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        data = engine.start_session(
            "proj",
            ClarifyRequest(prompt="訓練 LoRA 角色模型", modality=Modality.TRAINING),
        )
        assert data.session.state is ConsultantState.CLARIFY
        assert data.missing_slots  # all four required slots are missing

    def test_missing_slots_lists_all_four_initially(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        data = engine.start_session(
            "proj",
            ClarifyRequest(prompt="訓練 LoRA", modality=Modality.TRAINING),
        )
        missing = set(data.missing_slots)
        assert {"character_sheet", "dataset_pack", "training_recipe", "lora_preset"}.issubset(missing)

    def test_partial_slots_stay_in_clarify(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        started = engine.start_session(
            "proj",
            ClarifyRequest(prompt="訓練角色 LoRA", modality=Modality.TRAINING),
        )
        session_id = started.session.session_id

        # Fill only character_sheet — still missing three slots.
        step = engine.advance_session(
            "proj", session_id,
            ClarifyRequest(prompt="(continue)", modality=Modality.TRAINING),
            slots={"character_sheet": "cs-id-001"},
        )
        assert step.session.state is ConsultantState.CLARIFY
        assert "dataset_pack" in step.missing_slots
        assert "training_recipe" in step.missing_slots
        assert "lora_preset" in step.missing_slots

    def test_all_four_slots_advance_to_summary(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        started = engine.start_session(
            "proj",
            ClarifyRequest(prompt="訓練 LoRA", modality=Modality.TRAINING),
        )
        session_id = started.session.session_id

        step = engine.advance_session(
            "proj", session_id,
            ClarifyRequest(prompt="(continue)", modality=Modality.TRAINING),
            slots=_complete_training_slots(),
        )
        assert step.session.state is ConsultantState.SUMMARY
        assert step.missing_slots == []

    def test_i2v_recipe_slot_is_accepted_but_not_required(self, tmp_path: Path) -> None:
        """Supplying i2v_recipe in slots is allowed and accepted; absence is fine too."""
        engine = _engine(tmp_path)
        started = engine.start_session(
            "proj",
            ClarifyRequest(prompt="訓練 LoRA 並製作影片", modality=Modality.TRAINING),
        )
        session_id = started.session.session_id

        # Complete the required slots plus the optional i2v_recipe.
        all_slots = {**_complete_training_slots(), "i2v_recipe": "i2v-id-001"}
        step = engine.advance_session(
            "proj", session_id,
            ClarifyRequest(prompt="(continue)", modality=Modality.TRAINING),
            slots=all_slots,
        )
        # i2v_recipe is not in REQUIRED_SLOTS so the checklist is already
        # complete with just the four required keys.
        assert step.session.state is ConsultantState.SUMMARY
        # i2v_recipe key is NOT in REQUIRED_SLOTS whitelist; engine discards it.
        # Verify it was silently dropped (slot whitelist enforcement).
        assert "i2v_recipe" not in step.session.slots


# ---------------------------------------------------------------------------
# Plan output referencing entity IDs
# ---------------------------------------------------------------------------

class TestTrainingPlanEntityReferences:
    def _advance_to_generate(self, engine: ConsultantEngine, project_id: str) -> "ConsultantSessionData":  # noqa: F821
        from core.consultant.engine import ConsultantEngine  # noqa: F401 (unused here)
        started = engine.start_session(
            project_id,
            ClarifyRequest(prompt="訓練角色 LoRA 模型", modality=Modality.TRAINING),
        )
        session_id = started.session.session_id
        # Fill checklist to reach Summary.
        engine.advance_session(
            project_id, session_id,
            ClarifyRequest(prompt="(continue)", modality=Modality.TRAINING),
            slots=_complete_training_slots(),
        )
        # Summary -> Generate (plan is emitted here).
        return engine.advance_session(
            project_id, session_id,
            ClarifyRequest(prompt="(continue)", modality=Modality.TRAINING),
        )

    def test_plan_is_emitted_on_generate(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        data = self._advance_to_generate(engine, "proj")
        assert data.session.state is ConsultantState.GENERATE
        assert data.session.plan is not None

    def test_plan_is_training_flow(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        data = self._advance_to_generate(engine, "proj")
        assert data.session.plan is not None
        assert data.session.plan.is_training_flow is True

    def test_plan_has_entity_id_references(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        data = self._advance_to_generate(engine, "proj")
        plan = data.session.plan
        assert plan is not None
        assert plan.training_character_sheet_id == "cs-id-001"
        assert plan.training_dataset_pack_id == "dp-id-001"
        assert plan.training_recipe_id == "tr-id-001"
        assert plan.training_lora_preset_id == "lp-id-001"

    def test_plan_has_training_execution_steps(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        data = self._advance_to_generate(engine, "proj")
        plan = data.session.plan
        assert plan is not None
        assert len(plan.execution_steps) >= 6  # at minimum the §7.1 sequence steps
        step_titles = [s.title for s in plan.execution_steps]
        # Verify all four required §7.1.1 steps are present.
        assert any("CharacterSheet" in t for t in step_titles)
        assert any("DatasetPack" in t for t in step_titles)
        assert any("TrainingRecipe" in t for t in step_titles)
        assert any("LoraPreset" in t for t in step_titles)

    def test_plan_recommends_kohya_ss(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        data = self._advance_to_generate(engine, "proj")
        plan = data.session.plan
        assert plan is not None
        assert "kohya_ss" in plan.recommended_workers


# ---------------------------------------------------------------------------
# Suggestion-card emission
# ---------------------------------------------------------------------------

class TestSuggestionCardEmission:
    def _analyze(self, prompt: str, modality: Modality = Modality.TRAINING) -> "ConsultantAnalysis":  # noqa: F821
        result = build_clarify_result(
            ClarifyRequest(prompt=prompt, modality=modality),
            template_loaded=False,
        )
        assert result.analysis is not None
        return result.analysis

    def test_training_flow_emits_suggestion_cards(self) -> None:
        analysis = self._analyze("訓練 LoRA 角色模型")
        assert analysis.is_training_flow is True
        assert len(analysis.suggestion_cards) >= 4

    def test_all_four_required_entity_kinds_present(self) -> None:
        analysis = self._analyze("訓練 LoRA")
        kinds = {card.entity_kind for card in analysis.suggestion_cards}
        required = {"character_sheet", "dataset_pack", "training_recipe", "lora_preset"}
        assert required.issubset(kinds)

    def test_i2v_card_present_when_video_keyword(self) -> None:
        analysis = self._analyze("訓練 LoRA 並製作影片動畫")
        kinds = {card.entity_kind for card in analysis.suggestion_cards}
        assert "i2v_recipe" in kinds

    def test_i2v_card_absent_without_video_keyword(self) -> None:
        analysis = self._analyze("訓練 LoRA 角色模型")
        kinds = {card.entity_kind for card in analysis.suggestion_cards}
        assert "i2v_recipe" not in kinds

    def test_suggestion_card_schema_fields(self) -> None:
        analysis = self._analyze("訓練 LoRA 角色模型 trigger word 觸發詞")
        char_card = next(
            (c for c in analysis.suggestion_cards if c.entity_kind == "character_sheet"),
            None,
        )
        assert char_card is not None
        assert isinstance(char_card, TrainingSuggestionCard)
        assert char_card.action == "create"
        assert isinstance(char_card.prefilled, dict)
        assert char_card.reason  # non-empty reason string

    def test_character_name_prefilled_in_character_sheet_card(self) -> None:
        analysis = self._analyze("訓練「凱優香」的 LoRA 模型")
        char_card = next(
            (c for c in analysis.suggestion_cards if c.entity_kind == "character_sheet"),
            None,
        )
        assert char_card is not None
        assert char_card.prefilled.get("name") == "凱優香"

    def test_training_recipe_card_has_sensible_defaults(self) -> None:
        analysis = self._analyze("訓練 LoRA")
        recipe_card = next(
            (c for c in analysis.suggestion_cards if c.entity_kind == "training_recipe"),
            None,
        )
        assert recipe_card is not None
        assert recipe_card.prefilled.get("rank") == 32
        assert recipe_card.prefilled.get("epochs") == 10
        assert recipe_card.prefilled.get("optimizer") == "AdamW8bit"
        assert recipe_card.prefilled.get("caption_strategy") == "wd14"

    def test_existing_id_defaults_to_none(self) -> None:
        analysis = self._analyze("訓練 LoRA")
        for card in analysis.suggestion_cards:
            assert card.existing_id is None, (
                f"existing_id should default to None (no DB query at analysis time); "
                f"entity_kind={card.entity_kind}"
            )

    def test_non_training_prompt_has_no_suggestion_cards(self) -> None:
        result = build_clarify_result(
            ClarifyRequest(prompt="畫出角色立繪", modality=Modality.IMAGE),
            template_loaded=False,
        )
        assert result.analysis is not None
        assert result.analysis.suggestion_cards == []
        assert result.analysis.is_training_flow is False


# ---------------------------------------------------------------------------
# Training summary and next_step
# ---------------------------------------------------------------------------

class TestTrainingSummaryAndNextStep:
    def test_summary_mentions_training_flow(self) -> None:
        result = build_clarify_result(
            ClarifyRequest(prompt="訓練 LoRA 角色模型", modality=Modality.TRAINING),
            template_loaded=False,
        )
        assert "training" in result.summary.lower() or "Training" in result.summary

    def test_next_step_mentions_checklist_entities(self) -> None:
        result = build_clarify_result(
            ClarifyRequest(prompt="訓練 LoRA", modality=Modality.TRAINING),
            template_loaded=False,
        )
        assert "CharacterSheet" in result.next_step or "checklist" in result.next_step.lower()

    def test_questions_include_training_checklist(self) -> None:
        result = build_clarify_result(
            ClarifyRequest(prompt="訓練 LoRA", modality=Modality.TRAINING),
            template_loaded=False,
        )
        # At least the four §7.1.1 checklist questions should appear.
        assert len(result.questions) >= 4
        # Check that at least one question mentions each step.
        text = " ".join(result.questions)
        assert "CharacterSheet" in text or "角色" in text
        assert "DatasetPack" in text or "資料集" in text
        assert "TrainingRecipe" in text or "訓練配方" in text
        assert "LoraPreset" in text or "LoRA stack" in text


# ---------------------------------------------------------------------------
# Persistence across restart for training sessions
# ---------------------------------------------------------------------------

class TestTrainingSessionPersistence:
    def test_training_session_survives_restart(self, tmp_path: Path) -> None:
        project_dir = tmp_path / "my_project"
        project_dir.mkdir(parents=True, exist_ok=True)
        resolver = lambda _pid: project_dir  # noqa: E731

        engine_a = ConsultantEngine(sessions_path_resolver=resolver)
        started = engine_a.start_session(
            "proj",
            ClarifyRequest(prompt="訓練 LoRA 角色", modality=Modality.TRAINING),
        )
        session_id = started.session.session_id
        engine_a.advance_session(
            "proj", session_id,
            ClarifyRequest(prompt="(continue)", modality=Modality.TRAINING),
            slots={"character_sheet": "cs-id-001"},
        )

        # Simulate restart.
        engine_b = ConsultantEngine(sessions_path_resolver=resolver)
        resumed = engine_b.resume_session("proj")
        assert resumed is not None
        assert resumed.session_id == session_id
        assert resumed.state is ConsultantState.CLARIFY
        assert resumed.slots.get("character_sheet") == "cs-id-001"
        assert resumed.modality is Modality.TRAINING
