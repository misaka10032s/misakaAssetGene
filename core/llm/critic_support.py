"""Shared VLM-critic prompt building + strict JSON parsing (spec §5.15 / §3.2).

Lives in its OWN module, separate from both ``core.llm.vision`` (the
orchestrator) and ``core.llm.providers.{ollama,openai}`` (the two callers),
to avoid an import cycle: ``vision.py`` must import the provider
``critique_image`` functions to call them, and both providers need this
prompt/parsing logic — if it lived in ``vision.py`` the providers would need
to import back from it, and ``core``'s G4 import-cycle gate (acyclic
siblings) exists to catch exactly that shape of cycle.
"""

from __future__ import annotations

import json
import logging

from core.models.schemas import FidelityCheck, FidelityCheckResult

logger = logging.getLogger("misaka.llm.vision")


def build_critic_prompt(checks: list[FidelityCheck]) -> str:
    """Build the critic instruction text for one image-critique call.

    Lists every check's id/region/pass_criteria so the VLM can address them
    individually, and pins the exact JSON response shape the parser below
    expects (spec §3.2's ``FidelityCheckResult`` fields).
    """
    lines = [
        "You are a strict visual-fidelity critic for a character illustration.",
        "For EACH numbered check below, decide whether the image satisfies it.",
        "Respond with ONLY a JSON object of this exact shape, no prose outside it:",
        '{"results": [',
        '  {"id": "<check id>", "passed": true|false, "confidence": 0.0-1.0,',
        '   "region_bbox": [x0, y0, x1, y1] | null, "note": "<short reason>"}',
        "]}",
        "region_bbox, when given, must be pixel coordinates in THIS image "
        "(not a fraction), tightly bounding the specific area that fails.",
        "If a check passes, region_bbox may be null.",
        "",
        "Checks:",
    ]
    for check in checks:
        lines.append(
            f"- id={check.id!r} region={check.region_hint.value} "
            f"criterion={check.pass_criteria!r}"
        )
    return "\n".join(lines)


def parse_critic_response(raw_text: str, checks: list[FidelityCheck]) -> list[FidelityCheckResult]:
    """Parse a VLM critic's JSON response into one result per check.

    ANY parse failure (invalid JSON, wrong shape, a missing check id, an
    invalid field) falls back to an all-pass verdict for the affected
    check(s), with a warning logged — the critic's own output is untrusted
    input, not a program bug, so this never raises.
    """
    try:
        payload = json.loads(raw_text)
    except (json.JSONDecodeError, TypeError):
        logger.warning("fidelity critic: malformed JSON response, defaulting all checks to pass")
        return [_default_pass(check, "malformed critic JSON response") for check in checks]

    entries = _normalize_payload(payload)
    results: list[FidelityCheckResult] = []
    for check in checks:
        entry = entries.get(check.id)
        if entry is None:
            logger.warning("fidelity critic: no verdict for check %s, defaulting to pass", check.id)
            results.append(_default_pass(check, "no verdict returned for this check"))
            continue
        results.append(_parse_entry(check, entry))
    return results


def _normalize_payload(payload: object) -> dict[str, dict[str, object]]:
    if isinstance(payload, dict) and isinstance(payload.get("results"), list):
        payload = payload["results"]
    if isinstance(payload, list):
        normalized: dict[str, dict[str, object]] = {}
        for item in payload:
            if isinstance(item, dict) and isinstance(item.get("id"), str):
                normalized[item["id"]] = item
        return normalized
    if isinstance(payload, dict):
        return {key: value for key, value in payload.items() if isinstance(value, dict)}
    return {}


def _default_pass(check: FidelityCheck, note: str) -> FidelityCheckResult:
    return FidelityCheckResult(id=check.id, passed=True, confidence=0.0, region_bbox=None, note=note)


def _parse_entry(check: FidelityCheck, entry: dict[str, object]) -> FidelityCheckResult:
    passed = entry.get("passed")
    if not isinstance(passed, bool):
        return _default_pass(check, "missing/invalid 'passed' field")

    confidence = 0.5
    confidence_raw = entry.get("confidence", 0.5)
    try:
        confidence = min(1.0, max(0.0, float(confidence_raw)))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        confidence = 0.5

    bbox = _parse_bbox(entry.get("region_bbox"))
    note = str(entry.get("note") or "")
    return FidelityCheckResult(id=check.id, passed=passed, confidence=confidence, region_bbox=bbox, note=note)


def _parse_bbox(raw: object) -> tuple[int, int, int, int] | None:
    if not isinstance(raw, (list, tuple)) or len(raw) != 4:
        return None
    try:
        x0, y0, x1, y1 = (int(value) for value in raw)
    except (TypeError, ValueError):
        return None
    return (x0, y0, x1, y1)
