from __future__ import annotations

import httpx

from core.config import Settings
from core.models.schemas import ProviderMode, ProviderName, ProviderSnapshot, ProviderStatus, SynopsisOptimizeResult


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
