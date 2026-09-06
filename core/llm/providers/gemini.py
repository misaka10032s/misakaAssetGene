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


def build_snapshot(settings: Settings) -> ProviderSnapshot:
    configured = bool(settings.gemini_api_key)
    status = ProviderStatus.CONFIGURED if configured else ProviderStatus.DISABLED

    if configured:
        try:
            response = httpx.get(
                f"{settings.gemini_api_base_url.rstrip('/')}/models",
                timeout=3.0,
                params={"key": settings.gemini_api_key},
            )
            status = ProviderStatus.READY if response.is_success else ProviderStatus.UNAVAILABLE
        except httpx.HTTPError:
            status = ProviderStatus.UNAVAILABLE

    return ProviderSnapshot(
        name=ProviderName.GEMINI,
        mode=ProviderMode.CLOUD,
        status=status,
        configured=configured,
        base_url=settings.gemini_api_base_url,
    )


def optimize_synopsis(settings: Settings, prompt: str) -> SynopsisOptimizeResult | None:
    snapshot = build_snapshot(settings)
    if snapshot.status is not ProviderStatus.READY:
        return None

    try:
        response = httpx.post(
            f"{settings.gemini_api_base_url.rstrip('/')}/models/{settings.gemini_model}:generateContent",
            timeout=10.0,
            params={"key": settings.gemini_api_key},
            json={
                "contents": [
                    {
                        "parts": [{"text": prompt}],
                    }
                ],
                "generationConfig": {
                    "temperature": 0.2,
                },
            },
        )
        response.raise_for_status()
        payload = response.json()
    except httpx.HTTPError:
        return None

    candidates = payload.get("candidates") or []
    if not candidates:
        return None

    parts = candidates[0].get("content", {}).get("parts") or []
    content = "\n".join(str(part.get("text") or "").strip() for part in parts if part.get("text")).strip()
    if not content:
        return None

    return SynopsisOptimizeResult(
        optimized_synopsis=content,
        strategy=ProviderStatus.READY.value,
        provider=ProviderName.GEMINI,
    )


def critique_image(
    settings: Settings,
    image_bytes: bytes,
    checks: list[FidelityCheck],
) -> list[FidelityCheckResult] | None:
    """Spec §5.15 / §3.1 — VLM critique via Gemini ``generateContent``.

    Used two ways by ``core.llm.vision``: (1) as an ordinary entry in
    ``settings.llm_provider_order`` for the primary two-pass critique, and
    (2) as the gate #5 SECOND OPINION provider (``MISAKA_FIDELITY_SECOND_
    OPINION=gemini``, C5 fix 2026-09-06) called with only the subset of
    checks that need re-verification. Same request/response shape either
    way — the caller decides which checks to send.

    Sends the image as an ``inline_data`` part alongside the text prompt,
    and requests ``response_mime_type: application/json`` so Gemini returns
    a bare JSON body (mirrors ``format: json`` on the Ollama path / ``
    response_format: json_object`` on the OpenAI path). Returns ``None``
    (never raises) when not configured or unreachable, so the caller can
    fall through to the next provider / treat the second opinion as
    unavailable. This function never checks the network state itself — the
    caller (``core.llm.vision``) is responsible for the offline gate
    (``core.llm.router.gate_providers`` treats Gemini as ``ProviderMode.
    CLOUD``), same convention as ``core.llm.providers.openai.critique_image``.
    """
    if not settings.gemini_api_key:
        return None

    image_b64 = base64.b64encode(image_bytes).decode("ascii")
    try:
        response = httpx.post(
            f"{settings.gemini_api_base_url.rstrip('/')}/models/{settings.gemini_model}:generateContent",
            timeout=60.0,
            params={"key": settings.gemini_api_key},
            json={
                "contents": [
                    {
                        "parts": [
                            {"text": build_critic_prompt(checks)},
                            {"inline_data": {"mime_type": "image/png", "data": image_b64}},
                        ],
                    }
                ],
                "generationConfig": {
                    "temperature": 0.2,
                    "response_mime_type": "application/json",
                },
            },
        )
        response.raise_for_status()
        payload = response.json()
    except httpx.HTTPError:
        return None

    candidates = payload.get("candidates") or []
    if not candidates:
        return None

    parts = candidates[0].get("content", {}).get("parts") or []
    content = "\n".join(str(part.get("text") or "").strip() for part in parts if part.get("text")).strip()
    if not content:
        return None
    return parse_critic_response(content, checks)
