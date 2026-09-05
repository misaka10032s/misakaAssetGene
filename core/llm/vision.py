"""VLM fidelity-critic orchestration (spec §5.15 / C-spec.md §3).

Mirrors the handler-dict + ``settings.llm_provider_order`` pattern already
used by ``core.llm.service.optimize_synopsis`` — each configured provider is
tried in turn until one returns a result. Cloud providers (OpenAI) are
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
-parsed provider verdicts — never inside a provider module — so Ollama and
OpenAI verdicts are gated identically regardless of which one answered.
"""

from __future__ import annotations

import logging

from fastapi import HTTPException

from core.config import Settings
from core.llm.providers.ollama import critique_image as ollama_critique_image
from core.llm.providers.openai import critique_image as openai_critique_image
from core.models.schemas import BodyRegion, FidelityCheck, FidelityCheckResult
from core.network.state import NetworkState

logger = logging.getLogger("misaka.llm.vision")

# spec §3.4 gate 1: a fail whose bbox covers more than this fraction of the
# full image area is too vague to localize — downgrade to pass.
_MAX_FAIL_BBOX_AREA_FRACTION = 0.6

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

_CLOUD_PROVIDERS = {"openai", "chatgpt"}


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
    consistency — a fail only counts if BOTH passes agree) and applies the
    bbox-area / region-compatibility downgrades (gates 1 and 3) to the
    merged result. Raises ``HTTPException(409)`` when no configured
    provider answers, mirroring ``core.llm.service.optimize_synopsis``.
    """
    logger.info(
        "fidelity critic: sending image at native resolution "
        "(no Pillow available for §3.3 downscale, scale_factor=1.0)"
    )
    pass_a = _run_single_pass(settings, image_bytes, checks, network_state)
    pass_b = _run_single_pass(settings, image_bytes, checks, network_state)
    merged = _apply_two_pass_gate(pass_a, pass_b)

    checks_by_id = {check.id: check for check in checks}
    return [
        _apply_anti_hallucination_gates(result, checks_by_id[result.id], image_width, image_height)
        for result in merged
        if result.id in checks_by_id
    ]


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
) -> FidelityCheckResult:
    """spec §3.4: downgrade an unreliable ``fail`` to ``passed=True``.

    A ``passed=True`` result is never touched. Every downgrade is logged at
    INFO with the check id so a discarded fail is traceable, not silent.
    """
    if result.passed:
        return result

    if result.region_bbox is None:
        logger.info("fidelity gate: check %s fail discarded (unlocalized, no bbox)", check.id)
        return result.model_copy(update={"passed": True, "note": f"{result.note} [gate: unlocalized]".strip()})

    area_fraction = _bbox_area_fraction(result.region_bbox, image_width, image_height)
    if area_fraction > _MAX_FAIL_BBOX_AREA_FRACTION:
        logger.info(
            "fidelity gate: check %s fail discarded (bbox area %.0f%% > %.0f%% threshold)",
            check.id, area_fraction * 100, _MAX_FAIL_BBOX_AREA_FRACTION * 100,
        )
        return result.model_copy(update={"passed": True, "note": f"{result.note} [gate: bbox too large]".strip()})

    if not _region_compatible(check.region_hint, result.region_bbox, image_height):
        logger.info(
            "fidelity gate: check %s fail discarded (bbox incompatible with region_hint=%s)",
            check.id, check.region_hint.value,
        )
        return result.model_copy(update={"passed": True, "note": f"{result.note} [gate: region mismatch]".strip()})

    return result
