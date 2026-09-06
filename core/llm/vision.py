"""VLM fidelity-critic orchestration (spec §5.15 / C-spec.md §3).

Mirrors the handler-dict + ``settings.llm_provider_order`` pattern already
used by ``core.llm.service.optimize_synopsis`` — each configured provider is
tried in turn until one returns a result. Cloud providers (OpenAI/Gemini) are
skipped unless the effective network state is ``ONLINE`` (spec §3.1
"離線閘門沿用 router.py:19-35" — same offline-gates-cloud rule as
``core.llm.router.gate_providers``, applied here to an actual provider call
rather than just a UI status snapshot).

Downscaling (spec §3.3): this venv has no Pillow (deliberately not added —
see the dispatch brief), so the image is always sent at native resolution.
The "scale factor" the spec describes for rescaling a downsampled bbox back
to source pixels is therefore always ``1.0`` here — a returned bbox is
already assumed to be in source-image pixel coordinates. This gap is
recorded, not hidden: :func:`critique` logs it once per call at INFO.

Anti-hallucination gates (spec §3.4) are applied HERE, once, on the already
-parsed provider verdicts — never inside a provider module — so Ollama,
OpenAI and Gemini verdicts are gated identically regardless of which one
answered.

C5 fix (2026-09-06, fidelity-critic-second-opinion) — two demo runs measured
the SAME local critic (``qwen2.5vl:7b``) returning a confident false PASS on
a small/absent detail (demo1: 挑染/內衣; demo2 DEMO2-report.md: ``outfits-7``
"連身式無袖洋裝" passed at confidence 1.0 while the image clearly showed long
sleeves). The original three gates only ever guarded FALSE FAILS — nothing
stopped a confident false PASS from reaching ``passed=True``. Two new gates
close that gap:

- **Gate #4 (localized-pass required)** — a ``pass`` verdict with no
  ``region_bbox``, a bbox covering more than ``_MAX_BBOX_AREA_FRACTION`` of
  the image, or ``confidence`` below ``MISAKA_FIDELITY_PASS_MIN_CONFIDENCE``,
  is downgraded from ``pass`` to the new ``unverified`` tri-state
  (``FidelityCheckResult.verdict`` — see that model's docstring).
- **Gate #5 (second opinion for fine details)** — every ``unverified`` check
  PLUS every remaining ``pass`` whose ``FidelityCheck.fine_detail`` is
  ``True`` (sleeve/armhole/lace/brooch/collar/cuff/hem/inner-layer/highlight
  — exactly the class of detail demo2 got wrong) is re-sent to a SECOND,
  independent VLM provider (``MISAKA_FIDELITY_SECOND_OPINION``, default
  ``gemini``). Both agreeing on ``pass`` confirms it; disagreement becomes a
  confirmed ``fail`` (note carries both critics' reasoning); an unavailable
  second opinion (no key / offline / unreachable) leaves the check
  ``unverified`` rather than ever silently promoting it to ``pass``.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from fastapi import HTTPException

from core.config import Settings
from core.llm.providers.gemini import critique_image as gemini_critique_image
from core.llm.providers.ollama import critique_image as ollama_critique_image
from core.llm.providers.openai import critique_image as openai_critique_image
from core.models.schemas import BodyRegion, FidelityCheck, FidelityCheckResult
from core.network.state import NetworkState

logger = logging.getLogger("misaka.llm.vision")

# spec §3.4 gates #1/#4: a bbox covering more than this fraction of the full
# image area is too vague to localize — it downgrades a fail to pass (gate
# #1) and, symmetrically, downgrades a pass to unverified (gate #4, C5 fix).
_MAX_BBOX_AREA_FRACTION = 0.6

# spec §3.4 gate 3: coarse head-to-toe vertical band (fraction of image
# HEIGHT) used to sanity-check a returned bbox against its check's declared
# region_hint. A HEAD check whose bbox center falls in the bottom 20% (as
# the spec's own example puts it) is region-incompatible.
_REGION_VERTICAL_BAND: dict[BodyRegion, tuple[float, float]] = {
    BodyRegion.HEAD: (0.0, 0.35),
    BodyRegion.FACE: (0.0, 0.35),
    BodyRegion.TORSO: (0.2, 0.65),
    BodyRegion.WAIST: (0.45, 0.75),
    BodyRegion.LEGS: (0.6, 1.0),
    BodyRegion.BACKGROUND: (0.0, 1.0),
}

_CLOUD_PROVIDERS = {"openai", "chatgpt", "gemini"}

# spec §5.15 gate #5 (C5 fix): second-opinion providers a check needing
# re-verification may be routed to. Both are CLOUD (core.llm.router
# ProviderMode.CLOUD) — gated by network_state in ``_call_second_opinion``,
# never called offline.
_SECOND_OPINION_HANDLERS: dict[
    str, Callable[[Settings, bytes, list[FidelityCheck]], list[FidelityCheckResult] | None]
] = {
    "gemini": gemini_critique_image,
    "openai": openai_critique_image,
}


def critique(
    settings: Settings,
    image_bytes: bytes,
    checks: list[FidelityCheck],
    image_width: int,
    image_height: int,
    network_state: NetworkState = NetworkState.OFFLINE,
) -> list[FidelityCheckResult]:
    """Run the VLM critic against ``checks`` and return gated verdicts.

    Runs the underlying provider call TWICE (spec §3.4 gate 2: two-pass
    consistency — a fail only counts if BOTH passes agree), applies the
    bbox-area / region-compatibility / localized-pass downgrades (gates 1,
    3, 4) to the merged result, then routes any still-``unverified`` or
    ``fine_detail``-``pass`` check through a second-opinion provider (gate
    5). Raises ``HTTPException(409)`` when no configured PRIMARY provider
    answers, mirroring ``core.llm.service.optimize_synopsis`` — an
    unavailable SECOND-opinion provider never raises, it just leaves the
    affected checks ``unverified`` (see module docstring).
    """
    logger.info(
        "fidelity critic: sending image at native resolution "
        "(no Pillow available for §3.3 downscale, scale_factor=1.0)"
    )
    pass_a = _run_single_pass(settings, image_bytes, checks, network_state)
    pass_b = _run_single_pass(settings, image_bytes, checks, network_state)
    merged = _apply_two_pass_gate(pass_a, pass_b)

    checks_by_id = {check.id: check for check in checks}
    gated = [
        _apply_anti_hallucination_gates(result, checks_by_id[result.id], image_width, image_height, settings)
        for result in merged
        if result.id in checks_by_id
    ]
    return _apply_second_opinion_gate(settings, gated, checks_by_id, image_bytes, network_state)


def _run_single_pass(
    settings: Settings,
    image_bytes: bytes,
    checks: list[FidelityCheck],
    network_state: NetworkState,
) -> list[FidelityCheckResult]:
    provider_handlers = {
        "ollama": lambda: ollama_critique_image(settings, image_bytes, checks),
        "openai": lambda: openai_critique_image(settings, image_bytes, checks),
        "chatgpt": lambda: openai_critique_image(settings, image_bytes, checks),
        "gemini": lambda: gemini_critique_image(settings, image_bytes, checks),
    }

    for provider_name in settings.llm_provider_order:
        handler = provider_handlers.get(provider_name)
        if handler is None:
            continue
        if provider_name in _CLOUD_PROVIDERS and network_state != NetworkState.ONLINE:
            continue
        result = handler()
        if result is not None:
            return result

    raise HTTPException(
        status_code=409,
        detail=(
            "No ready fidelity-critic VLM provider is available. Install "
            "MISAKA_OLLAMA_VISION_MODEL in Ollama, or configure an OpenAI "
            "API key and go online."
        ),
    )


def _apply_two_pass_gate(
    pass_a: list[FidelityCheckResult],
    pass_b: list[FidelityCheckResult],
) -> list[FidelityCheckResult]:
    """spec §3.4 gate 2: a fail only counts if BOTH passes agree it fails.

    One fail + one pass on the same check id is downgraded to pass (the
    critic could not repeat its own verdict, so it is not trusted). Both
    fail -> the fail stands (pass A's fields are kept as the representative
    verdict). Any id present in only one pass (should not normally happen)
    passes through unchanged.
    """
    by_id_b = {result.id: result for result in pass_b}
    merged: list[FidelityCheckResult] = []
    for result_a in pass_a:
        result_b = by_id_b.get(result_a.id)
        if result_b is None:
            merged.append(result_a)
            continue
        if result_a.passed or result_b.passed:
            if not (result_a.passed and result_b.passed):
                merged.append(
                    result_a.model_copy(
                        update={
                            "verdict": "pass",
                            "passed": True,
                            "note": f"{result_a.note} [gate: two-pass disagreement]".strip(),
                        }
                    )
                )
            else:
                merged.append(result_a)
        else:
            merged.append(result_a)
    return merged


def _bbox_area_fraction(bbox: tuple[int, int, int, int], image_width: int, image_height: int) -> float:
    if image_width <= 0 or image_height <= 0:
        return 0.0
    x0, y0, x1, y1 = bbox
    area = max(0, x1 - x0) * max(0, y1 - y0)
    return area / float(image_width * image_height)


def _bbox_center_fraction_y(bbox: tuple[int, int, int, int], image_height: int) -> float:
    if image_height <= 0:
        return 0.0
    _, y0, _, y1 = bbox
    return ((y0 + y1) / 2.0) / float(image_height)


def _region_compatible(region: BodyRegion, bbox: tuple[int, int, int, int], image_height: int) -> bool:
    band_start, band_end = _REGION_VERTICAL_BAND.get(region, (0.0, 1.0))
    center_y = _bbox_center_fraction_y(bbox, image_height)
    return band_start <= center_y <= band_end


def _apply_anti_hallucination_gates(
    result: FidelityCheckResult,
    check: FidelityCheck,
    image_width: int,
    image_height: int,
    settings: Settings,
) -> FidelityCheckResult:
    """spec §3.4 gates #1-3: downgrade an unreliable ``fail`` to ``pass``;
    gate #4 (C5 fix): downgrade an unreliable ``pass`` to ``unverified``.

    Every downgrade is logged at INFO with the check id so it is traceable,
    never silent. Gate #4 runs on whatever comes OUT of gates #1-3 — a fail
    downgraded to pass by gate #1 (no bbox at all) or gate #2 (bbox too
    large) never had a trustworthy localization to begin with, so it
    legitimately lands on ``unverified`` rather than a blind ``pass``
    (module docstring).
    """
    staged = _apply_fail_side_gates(result, check, image_width, image_height)
    return _apply_localized_pass_gate(staged, image_width, image_height, settings)


def _apply_fail_side_gates(
    result: FidelityCheckResult,
    check: FidelityCheck,
    image_width: int,
    image_height: int,
) -> FidelityCheckResult:
    if result.verdict != "fail":
        return result

    if result.region_bbox is None:
        logger.info("fidelity gate: check %s fail discarded (unlocalized, no bbox)", check.id)
        return result.model_copy(
            update={"verdict": "pass", "passed": True, "note": f"{result.note} [gate: unlocalized]".strip()}
        )

    area_fraction = _bbox_area_fraction(result.region_bbox, image_width, image_height)
    if area_fraction > _MAX_BBOX_AREA_FRACTION:
        logger.info(
            "fidelity gate: check %s fail discarded (bbox area %.0f%% > %.0f%% threshold)",
            check.id, area_fraction * 100, _MAX_BBOX_AREA_FRACTION * 100,
        )
        return result.model_copy(
            update={"verdict": "pass", "passed": True, "note": f"{result.note} [gate: bbox too large]".strip()}
        )

    if not _region_compatible(check.region_hint, result.region_bbox, image_height):
        logger.info(
            "fidelity gate: check %s fail discarded (bbox incompatible with region_hint=%s)",
            check.id, check.region_hint.value,
        )
        return result.model_copy(
            update={"verdict": "pass", "passed": True, "note": f"{result.note} [gate: region mismatch]".strip()}
        )

    return result


def _apply_localized_pass_gate(
    result: FidelityCheckResult,
    image_width: int,
    image_height: int,
    settings: Settings,
) -> FidelityCheckResult:
    """spec §3.4 gate #4 (C5 fix, 2026-09-06): a ``pass`` this untrustworthy
    is never reported as a confirmed pass — downgrade to ``unverified``
    instead (never straight to ``fail``: the critic never actually asserted
    a failure here, so demoting all the way to ``fail`` would be inventing a
    verdict nobody gave)."""
    if result.verdict != "pass":
        return result

    if result.region_bbox is None:
        logger.info("fidelity gate: check %s pass unverified (no region_bbox to confirm it against)", result.id)
        return result.model_copy(
            update={
                "verdict": "unverified", "passed": False,
                "note": f"{result.note} [gate: unlocalized pass]".strip(),
            }
        )

    area_fraction = _bbox_area_fraction(result.region_bbox, image_width, image_height)
    if area_fraction > _MAX_BBOX_AREA_FRACTION:
        logger.info(
            "fidelity gate: check %s pass unverified (bbox area %.0f%% > %.0f%% threshold)",
            result.id, area_fraction * 100, _MAX_BBOX_AREA_FRACTION * 100,
        )
        return result.model_copy(
            update={
                "verdict": "unverified", "passed": False,
                "note": f"{result.note} [gate: bbox too large pass]".strip(),
            }
        )

    min_confidence = settings.misaka_fidelity_pass_min_confidence
    if result.confidence < min_confidence:
        logger.info(
            "fidelity gate: check %s pass unverified (confidence %.2f < %.2f threshold)",
            result.id, result.confidence, min_confidence,
        )
        return result.model_copy(
            update={
                "verdict": "unverified", "passed": False,
                "note": f"{result.note} [gate: low confidence]".strip(),
            }
        )

    return result


def _apply_second_opinion_gate(
    settings: Settings,
    results: list[FidelityCheckResult],
    checks_by_id: dict[str, FidelityCheck],
    image_bytes: bytes,
    network_state: NetworkState,
) -> list[FidelityCheckResult]:
    """spec §3.4 gate #5 (C5 fix, 2026-09-06): route a not-yet-fully-trusted
    ``pass`` through a SECOND, independent VLM provider before confirming it.

    Targets exactly two categories: every ``unverified`` result (gate #4
    already flagged these as an unconfirmed pass), and every remaining
    ``pass`` whose check is ``fine_detail`` (a confident local pass on a
    small/easy-to-miss detail is exactly the false-positive class measured
    live — module docstring). Every other result passes through untouched;
    this is deliberately narrow, never a blanket re-check of every check.

    ``settings.misaka_fidelity_second_opinion == "off"`` disables this gate
    entirely. An unconfigured/offline/unreachable second-opinion provider
    degrades gracefully — every targeted result stays (or becomes)
    ``unverified``, logged at INFO, never silently promoted to ``pass``
    just because a second opinion could not be obtained.
    """
    mode = settings.misaka_fidelity_second_opinion
    if mode == "off":
        return results

    needs_opinion = [
        result for result in results
        if result.verdict == "unverified"
        or (result.verdict == "pass" and _is_fine_detail_check(result, checks_by_id))
    ]
    if not needs_opinion:
        return results
    needs_opinion_ids = {result.id for result in needs_opinion}

    opinion_checks = [checks_by_id[result.id] for result in needs_opinion if result.id in checks_by_id]
    opinion_results = _call_second_opinion(settings, mode, image_bytes, opinion_checks, network_state)

    if opinion_results is None:
        logger.info(
            "fidelity second opinion: provider=%s unavailable, keeping %d check(s) unverified",
            mode, len(needs_opinion),
        )
        return [
            _mark_unverified_no_opinion(result) if result.id in needs_opinion_ids else result
            for result in results
        ]

    opinion_by_id = {opinion.id: opinion for opinion in opinion_results}
    return [
        _reconcile_second_opinion(result, opinion_by_id.get(result.id)) if result.id in needs_opinion_ids else result
        for result in results
    ]


def _is_fine_detail_check(result: FidelityCheckResult, checks_by_id: dict[str, FidelityCheck]) -> bool:
    check = checks_by_id.get(result.id)
    return check is not None and check.fine_detail


def _mark_unverified_no_opinion(result: FidelityCheckResult) -> FidelityCheckResult:
    if result.verdict == "unverified":
        return result
    return result.model_copy(
        update={
            "verdict": "unverified", "passed": False,
            "note": f"{result.note} [gate: second opinion unavailable]".strip(),
        }
    )


def _reconcile_second_opinion(
    result: FidelityCheckResult, opinion: FidelityCheckResult | None
) -> FidelityCheckResult:
    """spec §3.4 gate #5: ``pass`` only when BOTH critics agree; disagreement
    is a confirmed ``fail`` (note carries both critics' reasoning); a missing
    second-opinion verdict (e.g. the second provider didn't answer for this
    specific check id) keeps ``result`` unverified rather than guessing."""
    if opinion is None:
        return _mark_unverified_no_opinion(result)
    if opinion.passed:
        return result.model_copy(
            update={
                "verdict": "pass", "passed": True,
                "note": f"{result.note} [gate: second opinion agrees]".strip(),
            }
        )
    return result.model_copy(
        update={
            "verdict": "fail", "passed": False,
            "note": f"local: {result.note} | second opinion: {opinion.note}".strip(" |"),
        }
    )


def _call_second_opinion(
    settings: Settings,
    mode: str,
    image_bytes: bytes,
    checks: list[FidelityCheck],
    network_state: NetworkState,
) -> list[FidelityCheckResult] | None:
    handler = _SECOND_OPINION_HANDLERS.get(mode)
    if handler is None or not checks:
        return None
    if network_state != NetworkState.ONLINE:
        logger.info("fidelity second opinion: skipped (network state != ONLINE), provider=%s", mode)
        return None
    return handler(settings, image_bytes, checks)
