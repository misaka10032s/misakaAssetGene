from __future__ import annotations

import httpx

from core.config import Settings
from core.models.schemas import ProviderMode, ProviderName, ProviderSnapshot, ProviderStatus, SynopsisOptimizeResult


def build_snapshot(settings: Settings) -> ProviderSnapshot:
    status = ProviderStatus.CONFIGURED if settings.misaka_ollama_base_url else ProviderStatus.DISABLED
    configured = bool(settings.misaka_ollama_base_url)
    if configured:
        try:
            response = httpx.get(f"{settings.misaka_ollama_base_url.rstrip('/')}/api/tags", timeout=2.0)
            if response.is_success:
                status = ProviderStatus.READY
            else:
                status = ProviderStatus.UNAVAILABLE
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
        response = httpx.post(
            f"{settings.misaka_ollama_base_url.rstrip('/')}/api/generate",
            timeout=10.0,
            json={
                "model": settings.misaka_ollama_model,
                "prompt": prompt,
                "stream": False,
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
