from __future__ import annotations

import httpx

from core.config import Settings
from core.models.schemas import ProviderMode, ProviderName, ProviderSnapshot, ProviderStatus, SynopsisOptimizeResult


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
