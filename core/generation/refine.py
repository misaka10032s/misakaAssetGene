"""Image refine planner: §6.2 strategy decision tree + §5.11 decomposition.

This module is deliberately pure (no I/O, no network). Given a natural-language
refine instruction plus optional explicit signals (params / mask), it selects
the *minimal but sufficient* refine method from the spec §6.2 ladder:

    metadata-only -> parameter retune -> img2img -> inpaint / local edit -> full regen

and, for long multi-aspect instructions, splits the prompt into the staged
passes defined by spec §5.11 (base composition -> character detail ->
prop/accessory -> final polish). The :class:`RefinePlan` it returns is recorded
on the refine job so every refinement keeps "why this method, what changed
relative to the parent" (spec §5.11 / §6.2 final paragraph).
"""

from __future__ import annotations

import re

from core.models.schemas import (
    GenerationRecipe,
    PromptDecompositionPass,
    PromptDecompositionStep,
    RefinePlan,
    RefineRequest,
    RefineStrategy,
)

# Default img2img denoise when the instruction is a semantic-but-global edit.
# Low enough to preserve composition, high enough to honour the change.
DEFAULT_IMG2IMG_DENOISE = 0.5
# Inpaint reworks only the masked region, so it can denoise harder.
DEFAULT_INPAINT_DENOISE = 0.7

# Sampler/recipe params the §6.2 planner is allowed to tune. Anything else in
# the request params is ignored to keep the workflow graph well-formed.
TUNABLE_PARAMS = {
    "sampler",
    "sampler_name",
    "scheduler",
    "cfg",
    "steps",
    "seed",
    "denoise",
    "resolution",
    "width",
    "height",
    "upscaler",
}

# Keyword signals (zh-TW + en) used by the decision tree. Kept here so the
# heuristics are auditable in one place rather than scattered inline.
_METADATA_KEYWORDS = (
    "標籤", "標記", "tag", "最愛", "favorite", "favourite", "備註", "note", "重新命名", "rename", "收藏",
)
_LOCAL_EDIT_KEYWORDS = (
    "局部", "只把", "只改", "把左", "把右", "去掉", "移除", "remove", "inpaint", "遮罩", "mask",
    "其他不要動", "其它不要動", "保留其他", "這個區域", "選取區域",
)
_REGEN_KEYWORDS = (
    "重畫", "重新生成", "整張重", "全部重", "重新來", "regenerate", "from scratch", "構圖完全",
)
# Aspect markers that, together with overall length, indicate a multi-pass
# decomposition is warranted (spec §5.11).
_DECOMP_LENGTH_THRESHOLD = 40
_BASE_MARKERS = ("構圖", "鏡位", "背景", "場景", "站在", "廣場", "composition", "background", "camera")
_DETAIL_MARKERS = ("表情", "髮", "服裝", "禮服", "穿著", "手勢", "姿勢", "expression", "outfit", "hair", "pose")
_PROP_MARKERS = ("帽", "武器", "劍", "配件", "腰包", "道具", "prop", "weapon", "accessory", "hat")
_POLISH_MARKERS = ("色調", "光線", "色彩", "補光", "polish", "lighting", "color", "colour", "潤飾", "調暖", "調亮")


def _has_any(text: str, keywords: tuple[str, ...]) -> bool:
    lowered = text.lower()
    return any(keyword.lower() in lowered for keyword in keywords)


def _filter_params(params: dict[str, object]) -> dict[str, object]:
    return {key: value for key, value in params.items() if key in TUNABLE_PARAMS}


def select_strategy(request: RefineRequest) -> RefineStrategy:
    """Pick the minimal sufficient refine strategy (spec §6.2 decision tree).

    Precedence, cheapest first, but explicit signals win:
    1. explicit ``strategy`` -> honoured verbatim
    2. an attached mask or a local-edit instruction -> inpaint
    3. an explicit regenerate request -> full regen
    4. a pure metadata edit (tag/note/favorite) -> metadata-only
    5. tunable sampler params with no semantic edit -> parameter retune
    6. otherwise a semantic global edit -> img2img
    """
    if request.strategy is not None:
        return request.strategy

    instruction = request.instruction or ""
    tunable = _filter_params(request.params)

    if request.mask_asset_id or _has_any(instruction, _LOCAL_EDIT_KEYWORDS):
        return RefineStrategy.INPAINT
    if _has_any(instruction, _REGEN_KEYWORDS):
        return RefineStrategy.FULL_REGEN
    if _has_any(instruction, _METADATA_KEYWORDS):
        return RefineStrategy.METADATA_ONLY
    if tunable:
        return RefineStrategy.PARAM_RETUNE
    return RefineStrategy.IMG2IMG


def _recipe_for(strategy: RefineStrategy) -> GenerationRecipe | None:
    return {
        RefineStrategy.METADATA_ONLY: None,
        RefineStrategy.PARAM_RETUNE: GenerationRecipe.TXT2IMG,
        RefineStrategy.IMG2IMG: GenerationRecipe.IMG2IMG,
        RefineStrategy.INPAINT: GenerationRecipe.INPAINT,
        RefineStrategy.FULL_REGEN: GenerationRecipe.TXT2IMG,
    }[strategy]


def _reason_for(strategy: RefineStrategy, instruction: str) -> str:
    reasons = {
        RefineStrategy.METADATA_ONLY: "Metadata-only edit: tags / note / favorite change with no pixel change (spec §6.2).",
        RefineStrategy.PARAM_RETUNE: "Parameter retune: re-sample with adjusted sampler params, no source image needed (spec §6.2).",
        RefineStrategy.IMG2IMG: "img2img: global semantic edit that should preserve the existing composition (spec §6.2).",
        RefineStrategy.INPAINT: "Inpaint / local edit: only the masked region is reworked (spec §6.2).",
        RefineStrategy.FULL_REGEN: "Full regenerate: composition itself must change, so re-run from prompt (spec §6.2).",
    }
    return f"{reasons[strategy]} Instruction: {instruction.strip()}"


def decompose_prompt(instruction: str) -> list[PromptDecompositionStep]:
    """Split a long, multi-aspect instruction into staged passes (spec §5.11).

    Returns an empty list for short / single-aspect instructions; those are
    handled in a single pass. When decomposition applies, passes are emitted in
    the canonical order base composition -> character detail -> prop / accessory
    -> final polish, including only the stages the instruction actually touches.
    """
    text = instruction.strip()
    if len(text) < _DECOMP_LENGTH_THRESHOLD:
        return []

    aspect_count = sum(
        1
        for markers in (_BASE_MARKERS, _DETAIL_MARKERS, _PROP_MARKERS, _POLISH_MARKERS)
        if _has_any(text, markers)
    )
    # A single touched aspect is not worth a multi-pass plan.
    if aspect_count < 2:
        return []

    segments = [seg.strip() for seg in re.split(r"[；;。\n]+", text) if seg.strip()]
    stage_markers: list[tuple[PromptDecompositionPass, tuple[str, ...]]] = [
        (PromptDecompositionPass.BASE_COMPOSITION, _BASE_MARKERS),
        (PromptDecompositionPass.CHARACTER_DETAIL, _DETAIL_MARKERS),
        (PromptDecompositionPass.PROP_ACCESSORY, _PROP_MARKERS),
        (PromptDecompositionPass.FINAL_POLISH, _POLISH_MARKERS),
    ]

    steps: list[PromptDecompositionStep] = []
    for stage, markers in stage_markers:
        matched = [seg for seg in segments if _has_any(seg, markers)]
        if matched:
            steps.append(PromptDecompositionStep(stage=stage, prompt="；".join(matched)))
    return steps


def plan_refine(request: RefineRequest) -> RefinePlan:
    """Resolve a full refine plan from a refine request (spec §5.11 / §6.2)."""
    strategy = select_strategy(request)
    recipe = _recipe_for(strategy)
    instruction = request.instruction or ""
    tunable = _filter_params(request.params)

    params: dict[str, object] = dict(tunable)
    param_delta: dict[str, object] = dict(tunable)

    if strategy is RefineStrategy.IMG2IMG:
        params.setdefault("denoise", DEFAULT_IMG2IMG_DENOISE)
    elif strategy is RefineStrategy.INPAINT:
        params.setdefault("denoise", DEFAULT_INPAINT_DENOISE)

    decomposition = decompose_prompt(instruction) if recipe is not None else []

    return RefinePlan(
        strategy=strategy,
        recipe=recipe,
        reason=_reason_for(strategy, instruction),
        params=params,
        param_delta=param_delta,
        prompt_delta=instruction.strip(),
        decomposition=decomposition,
        requires_mask=strategy is RefineStrategy.INPAINT,
    )
