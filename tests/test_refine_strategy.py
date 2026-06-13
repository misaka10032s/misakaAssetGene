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


# ---------------------------------------------------------------------------
# §5.11 decompose_prompt dedup tests (M5.5)
# ---------------------------------------------------------------------------

def test_dedup_segment_appears_in_only_one_stage() -> None:
    # §5.11 dedup: a segment whose text matches both CHARACTER_DETAIL and
    # PROP_ACCESSORY markers (e.g. contains "服裝" and "帽") must appear in
    # only the earlier stage (CHARACTER_DETAIL wins).
    instruction = (
        "角色站在城堡前的廣場，黃昏鏡位俯視；"
        "她穿著紅帽配禮服、表情自信；"  # matches DETAIL (服裝) and PROP (帽)
        "最後把整體色調調暖，補光線。"
    )
    steps = refine.decompose_prompt(instruction)
    # Collect all segment tokens emitted across all steps
    all_prompts = "".join(step.prompt for step in steps)
    # The segment text must not be repeated verbatim
    segment = "她穿著紅帽配禮服、表情自信"
    occurrences = all_prompts.count(segment)
    assert occurrences == 1, f"Segment appeared {occurrences} times; expected 1 (dedup failed)"
    # And it must be in CHARACTER_DETAIL (earliest matching stage), not PROP_ACCESSORY
    stages = {step.stage: step.prompt for step in steps}
    assert PromptDecompositionPass.CHARACTER_DETAIL in stages
    assert segment in stages[PromptDecompositionPass.CHARACTER_DETAIL]
    if PromptDecompositionPass.PROP_ACCESSORY in stages:
        assert segment not in stages[PromptDecompositionPass.PROP_ACCESSORY]


def test_dedup_distinct_passes_unchanged() -> None:
    # §5.11 dedup: an instruction with truly distinct segments (each touching
    # only one stage) must not be over-deduped — all stages preserved.
    instruction = (
        "角色站在城堡廣場背景，鏡位俯視；"  # BASE_COMPOSITION only
        "她的髮型是長髮、表情自信；"         # CHARACTER_DETAIL only
        "手拿銀色長劍、腰間武器；"            # PROP_ACCESSORY only
        "最後色調調暖、整體補光線。"           # FINAL_POLISH only
    )
    steps = refine.decompose_prompt(instruction)
    stage_set = {step.stage for step in steps}
    assert PromptDecompositionPass.BASE_COMPOSITION in stage_set
    assert PromptDecompositionPass.CHARACTER_DETAIL in stage_set
    assert PromptDecompositionPass.PROP_ACCESSORY in stage_set
    assert PromptDecompositionPass.FINAL_POLISH in stage_set
    # Verify no step appears more than once
    assert len(steps) == len({step.stage for step in steps})


def test_dedup_preserves_canonical_order() -> None:
    # §5.11: after dedup, surviving stages must still be in canonical order.
    instruction = (
        "角色站在廣場，鏡位俯視背景；"
        "她的禮服紅色服裝帽子武器都要改；"  # DETAIL + PROP overlapping — DETAIL wins
        "整體色調調暖、補光。"
    )
    steps = refine.decompose_prompt(instruction)
    order = list(PromptDecompositionPass)
    indices = [order.index(step.stage) for step in steps]
    assert indices == sorted(indices), "Stages are not in canonical order after dedup"


def test_dedup_no_stage_emitted_twice() -> None:
    # A stage must appear at most once in the output regardless of how many
    # segments match it (they are joined, not split into multiple steps).
    instruction = (
        "站在廣場，背景構圖，另一個場景鏡位也要保留；"  # two BASE_COMPOSITION segments
        "整體色調調暖、補光線。"                           # FINAL_POLISH
    )
    steps = refine.decompose_prompt(instruction)
    stages = [step.stage for step in steps]
    assert len(stages) == len(set(stages)), "Same stage emitted more than once"


def test_dedup_lineage_metadata_preserved() -> None:
    # plan_refine returns the decomposition inside RefinePlan; stage + prompt
    # must be accessible for lineage / audit (spec §5.11 last bullet).
    instruction = (
        "角色站在城堡前的廣場，黃昏鏡位俯視；她穿著紅色禮服、表情自信、長髮飄動；"
        "手上拿著一把銀色長劍，腰間有皮革腰包；最後把整體色調調暖，補一下臉部光線。"
    )
    plan = refine.plan_refine(RefineRequest(instruction=instruction))
    for step in plan.decomposition:
        assert step.stage is not None, "stage must be set for lineage"
        assert step.prompt, "prompt must be non-empty for lineage"
