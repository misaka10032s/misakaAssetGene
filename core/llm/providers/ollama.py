from __future__ import annotations

import base64

import httpx

from core.config import Settings
from core.llm.critic_support import build_critic_prompt, parse_critic_response
from core.models.schemas import (
    FidelityCheck,
    FidelityCheckResult,
    ProviderMode,
    ProviderName,
    ProviderSnapshot,
    ProviderStatus,
    SynopsisOptimizeResult,
)


def list_models(settings: Settings) -> list[str]:
    response = httpx.get(f"{settings.misaka_ollama_base_url.rstrip('/')}/api/tags", timeout=2.0)
    response.raise_for_status()
    payload = response.json()
    return [str(model.get("name") or "").strip() for model in payload.get("models") or [] if model.get("name")]


def build_snapshot(settings: Settings) -> ProviderSnapshot:
    status = ProviderStatus.CONFIGURED if settings.misaka_ollama_base_url else ProviderStatus.DISABLED
    configured = bool(settings.misaka_ollama_base_url)
    if configured:
        try:
            status = ProviderStatus.READY if list_models(settings) else ProviderStatus.CONFIGURED
        except httpx.HTTPError:
            status = ProviderStatus.UNAVAILABLE

    return ProviderSnapshot(
        name=ProviderName.OLLAMA,
        mode=ProviderMode.LOCAL,
        status=status,
        configured=configured,
        base_url=settings.misaka_ollama_base_url,
    )


def optimize_synopsis(settings: Settings, prompt: str) -> SynopsisOptimizeResult | None:
    snapshot = build_snapshot(settings)
    if snapshot.status is not ProviderStatus.READY:
        return None
    try:
        available_models = list_models(settings)
        if not available_models:
            return None
        selected_model = settings.misaka_ollama_model if settings.misaka_ollama_model in available_models else available_models[0]
        response = httpx.post(
            f"{settings.misaka_ollama_base_url.rstrip('/')}/api/generate",
            timeout=10.0,
            json={
                "model": selected_model,
                "system": (
                    "You improve project synopsis text. Keep the original language. "
                    "Do not invent facts. Return one paragraph only."
                ),
                "prompt": prompt,
                "stream": False,
                "options": {
                    "temperature": 0.2,
                    "num_predict": 160,
                },
            },
        )
        response.raise_for_status()
        payload = response.json()
    except httpx.HTTPError:
        return None

    content = str(payload.get("response") or "").strip()
    if not content:
        return None

    return SynopsisOptimizeResult(
        optimized_synopsis=content,
        strategy=ProviderStatus.READY.value,
        provider=ProviderName.OLLAMA,
    )


# Response token budget scales with checklist size. Measured 2026-09-05
# against the REAL 19-check "夏目茶依子/default" checklist: a fixed
# ``num_predict=800`` (the C-spec.md §3.1 placeholder value, itself flagged
# there as "假設，Brief 1 驗收時量測" — i.e. provisional pending this exact
# measurement) truncated mid-JSON (Ollama ``done_reason="length"``),
# producing an UNPARSABLE response for all 19 checks every time.
_TOKENS_PER_CHECK = 200
_BASE_TOKEN_BUDGET = 400
_MAX_NUM_PREDICT = 6000

# Same 2026-09-05 measurement also found ``num_predict`` alone was NOT
# sufficient: the request also hit ``done_reason="length"`` well under the
# raised num_predict budget (only ~850 tokens generated) because Ollama's
# per-request CONTEXT window (prompt text + the image's own vision tokens +
# generation) defaults far below what a ~1000-token prompt plus a
# 896x1152 image needs. Explicitly requesting a larger ``num_ctx`` fixed it
# (3/3 repeats: valid JSON, ``done_reason="stop"``, 14.7-17.1s wall-clock) —
# without it, num_predict was silently starved by context, not itself.
_NUM_CTX = 8192


def _num_predict_for(checks: list[FidelityCheck]) -> int:
    return min(_MAX_NUM_PREDICT, _BASE_TOKEN_BUDGET + _TOKENS_PER_CHECK * len(checks))


def critique_image(
    settings: Settings,
    image_bytes: bytes,
    checks: list[FidelityCheck],
) -> list[FidelityCheckResult] | None:
    """Spec §5.15 / §3.1 — one VLM critique pass via Ollama ``/api/chat``.

    Returns ``None`` (never raises) when Ollama itself is unreachable or the
    configured ``MISAKA_OLLAMA_VISION_MODEL`` is not installed, so the
    caller (``core.llm.vision``) can fall through to the next provider in
    ``settings.llm_provider_order``. Uses ``keep_alive: 0`` so the vision
    model is unloaded immediately after the call (spec §8 VRAM contention
    note — it competes with SDXL for the same GPU memory).
    """
    if not settings.misaka_ollama_base_url:
        return None
    try:
        available_models = list_models(settings)
    except httpx.HTTPError:
        return None
    if settings.misaka_ollama_vision_model not in available_models:
        return None

    image_b64 = base64.b64encode(image_bytes).decode("ascii")
    try:
        response = httpx.post(
            f"{settings.misaka_ollama_base_url.rstrip('/')}/api/chat",
            timeout=60.0,
            json={
                "model": settings.misaka_ollama_vision_model,
                "messages": [
                    {
                        "role": "user",
                        "content": build_critic_prompt(checks),
                        "images": [image_b64],
                    }
                ],
                "format": "json",
                "stream": False,
                "options": {
                    "temperature": 0.2,
                    "num_predict": _num_predict_for(checks),
                    "num_ctx": _NUM_CTX,
                },
                "keep_alive": 0,
            },
        )
        response.raise_for_status()
        payload = response.json()
    except httpx.HTTPError:
        return None

    content = str(payload.get("message", {}).get("content") or "").strip()
    if not content:
        return None
    return parse_critic_response(content, checks)
