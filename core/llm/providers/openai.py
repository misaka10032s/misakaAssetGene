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
    configured = bool(settings.openai_api_key)
    status = ProviderStatus.CONFIGURED if configured else ProviderStatus.DISABLED

    if configured:
        try:
            response = httpx.get(
                f"{settings.openai_api_base_url.rstrip('/')}/models",
                timeout=3.0,
                headers={"authorization": f"Bearer {settings.openai_api_key}"},
            )
            status = ProviderStatus.READY if response.is_success else ProviderStatus.UNAVAILABLE
        except httpx.HTTPError:
            status = ProviderStatus.UNAVAILABLE

    return ProviderSnapshot(
        name=ProviderName.OPENAI,
        mode=ProviderMode.CLOUD,
        status=status,
        configured=configured,
        base_url=settings.openai_api_base_url,
    )


def optimize_synopsis(settings: Settings, prompt: str) -> SynopsisOptimizeResult | None:
    snapshot = build_snapshot(settings)
    if snapshot.status is not ProviderStatus.READY:
        return None

    try:
        response = httpx.post(
            f"{settings.openai_api_base_url.rstrip('/')}/chat/completions",
            timeout=10.0,
            headers={
                "authorization": f"Bearer {settings.openai_api_key}",
                "content-type": "application/json",
            },
            json={
                "model": settings.openai_model,
                "temperature": 0.2,
                "messages": [{"role": "user", "content": prompt}],
            },
        )
        response.raise_for_status()
        payload = response.json()
    except httpx.HTTPError:
        return None

    choices = payload.get("choices") or []
    if not choices:
        return None

    content = str(choices[0].get("message", {}).get("content") or "").strip()
    if not content:
        return None

    return SynopsisOptimizeResult(
        optimized_synopsis=content,
        strategy=ProviderStatus.READY.value,
        provider=ProviderName.OPENAI,
    )


def critique_image(
    settings: Settings,
    image_bytes: bytes,
    checks: list[FidelityCheck],
) -> list[FidelityCheckResult] | None:
    """Spec §5.15 / §3.1 — cloud fallback VLM critique via Chat Completions.

    Sends the image as a base64 data URI (``image_url`` content part).
    Returns ``None`` (never raises) when not configured or unreachable, so
    the caller can report "no provider available" itself. The caller
    (``core.llm.vision``) is responsible for the network-state gate — this
    function does not know whether cloud access is currently allowed.
    """
    if not settings.openai_api_key:
        return None

    image_b64 = base64.b64encode(image_bytes).decode("ascii")
    data_uri = f"data:image/png;base64,{image_b64}"
    try:
        response = httpx.post(
            f"{settings.openai_api_base_url.rstrip('/')}/chat/completions",
            timeout=60.0,
            headers={
                "authorization": f"Bearer {settings.openai_api_key}",
                "content-type": "application/json",
            },
            json={
                "model": settings.openai_model,
                "temperature": 0.2,
                "response_format": {"type": "json_object"},
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": build_critic_prompt(checks)},
                            {"type": "image_url", "image_url": {"url": data_uri}},
                        ],
                    }
                ],
            },
        )
        response.raise_for_status()
        payload = response.json()
    except httpx.HTTPError:
        return None

    choices = payload.get("choices") or []
    if not choices:
        return None

    content = str(choices[0].get("message", {}).get("content") or "").strip()
    if not content:
        return None
    return parse_critic_response(content, checks)
