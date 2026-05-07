from __future__ import annotations

import httpx

from core.config import Settings
from core.models.schemas import ProviderMode, ProviderName, ProviderSnapshot, ProviderStatus, SynopsisOptimizeResult


def build_snapshot(settings: Settings) -> ProviderSnapshot:
    configured = bool(settings.anthropic_api_key)
    status = ProviderStatus.CONFIGURED if configured else ProviderStatus.DISABLED

    if configured:
        try:
            response = httpx.get(
                f"{settings.anthropic_api_base_url.rstrip('/')}/v1/models",
                timeout=3.0,
                headers={
                    "x-api-key": settings.anthropic_api_key,
                    "anthropic-version": "2023-06-01",
                },
            )
            if response.is_success:
                status = ProviderStatus.READY
            else:
                status = ProviderStatus.UNAVAILABLE
        except httpx.HTTPError:
            status = ProviderStatus.UNAVAILABLE

    return ProviderSnapshot(
        name=ProviderName.ANTHROPIC,
        mode=ProviderMode.CLOUD,
        status=status,
        configured=configured,
        base_url=settings.anthropic_api_base_url,
    )


def optimize_synopsis(settings: Settings, prompt: str) -> SynopsisOptimizeResult | None:
    snapshot = build_snapshot(settings)
    if snapshot.status is not ProviderStatus.READY:
        return None

    try:
        response = httpx.post(
            f"{settings.anthropic_api_base_url.rstrip('/')}/v1/messages",
            timeout=10.0,
            headers={
                "x-api-key": settings.anthropic_api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": settings.anthropic_model,
                "max_tokens": 300,
                "temperature": 0.2,
                "messages": [
                    {
                        "role": "user",
                        "content": prompt,
                    }
                ],
            },
        )
        response.raise_for_status()
        payload = response.json()
    except httpx.HTTPError:
        return None

    content_blocks = payload.get("content") or []
    if not content_blocks:
        return None

    optimized_synopsis = str(content_blocks[0].get("text") or "").strip()
    if not optimized_synopsis:
        return None

    return SynopsisOptimizeResult(
        optimized_synopsis=optimized_synopsis,
        strategy=ProviderStatus.READY.value,
        provider=ProviderName.ANTHROPIC,
    )
