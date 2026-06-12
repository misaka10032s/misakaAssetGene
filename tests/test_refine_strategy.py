"""Unit tests for the image refine strategy decision tree (spec §6.2)
and multi-stage prompt decomposition (spec §5.11)."""

from __future__ import annotations

from core.generation import refine
from core.models.schemas import (
    GenerationRecipe,
    PromptDecompositionPass,
    RefineRequest,
    RefineStrategy,
)


def test_explicit_strategy_is_respected() -> None:
    plan = refine.plan_refine(RefineRequest(instruction="調整光線", strategy=RefineStrategy.IMG2IMG))
    assert plan.strategy is RefineStrategy.IMG2IMG
    assert plan.recipe is GenerationRecipe.IMG2IMG


def test_mask_present_selects_inpaint() -> None:
    # §6.2: a local-edit signal (a mask) escalates to inpaint, not full regen.
    plan = refine.plan_refine(RefineRequest(instruction="把帽子改成紅色", mask_asset_id="mask-1"))
    assert plan.strategy is RefineStrategy.INPAINT
    assert plan.recipe is GenerationRecipe.INPAINT
    assert plan.requires_mask is True


def test_param_only_request_selects_param_retune() -> None:
    # §6.2: tunable sampler params with no semantic edit -> parameter retune.
    plan = refine.plan_refine(RefineRequest(instruction="重新採樣一次", params={"cfg": 6, "steps": 30}))
    assert plan.strategy is RefineStrategy.PARAM_RETUNE
    assert plan.recipe is GenerationRecipe.TXT2IMG
    assert plan.param_delta == {"cfg": 6, "steps": 30}


def test_metadata_only_for_tag_note_edits() -> None:
    # §6.2: the cheapest rung when nothing about the pixels changes.
    plan = refine.plan_refine(RefineRequest(instruction="把這張標記為最愛並加上 #warm 標籤"))
    assert plan.strategy is RefineStrategy.METADATA_ONLY
    assert plan.recipe is None


def test_local_edit_keyword_selects_inpaint_even_without_mask() -> None:
    plan = refine.plan_refine(RefineRequest(instruction="只把左邊的杯子去掉，其他不要動"))
    assert plan.strategy is RefineStrategy.INPAINT
    assert plan.requires_mask is True


def test_semantic_edit_defaults_to_img2img() -> None:
    plan = refine.plan_refine(RefineRequest(instruction="讓整體氛圍更溫暖一點，手再抬高一點"))
    assert plan.strategy is RefineStrategy.IMG2IMG
    assert plan.recipe is GenerationRecipe.IMG2IMG


def test_regenerate_keyword_selects_full_regen() -> None:
    plan = refine.plan_refine(RefineRequest(instruction="整張重畫，構圖完全不同"))
    assert plan.strategy is RefineStrategy.FULL_REGEN
    assert plan.recipe is GenerationRecipe.TXT2IMG


def test_img2img_default_denoise_in_params() -> None:
    plan = refine.plan_refine(RefineRequest(instruction="氛圍更溫暖一點"))
    assert "denoise" in plan.params
    assert 0.0 < plan.params["denoise"] < 1.0


def test_long_prompt_triggers_decomposition() -> None:
    # §5.11: long, multi-aspect instructions are split into staged passes.
    instruction = (
        "角色站在城堡前的廣場，黃昏鏡位俯視；她穿著紅色禮服、表情自信、長髮飄動；"
        "手上拿著一把銀色長劍，腰間有皮革腰包；最後把整體色調調暖，補一下臉部光線。"
    )
    plan = refine.plan_refine(RefineRequest(instruction=instruction))
    stages = [step.stage for step in plan.decomposition]
    assert PromptDecompositionPass.BASE_COMPOSITION in stages
    assert PromptDecompositionPass.FINAL_POLISH in stages
    # Stages must be ordered base -> detail -> prop -> polish.
    order = list(PromptDecompositionPass)
    indices = [order.index(stage) for stage in stages]
    assert indices == sorted(indices)


def test_short_prompt_has_no_decomposition() -> None:
    plan = refine.plan_refine(RefineRequest(instruction="調暖一點"))
    assert plan.decomposition == []


def test_prompt_delta_records_instruction() -> None:
    plan = refine.plan_refine(RefineRequest(instruction="手再抬高一點"))
    assert "手再抬高一點" in plan.prompt_delta
