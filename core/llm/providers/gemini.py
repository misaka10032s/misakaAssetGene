from __future__ import annotations

import httpx

from core.config import Settings
from core.models.schemas import ProviderMode, ProviderName, ProviderSnapshot, ProviderStatus, SynopsisOptimizeResult


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
                ]
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
