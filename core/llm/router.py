from core.config import Settings
from core.llm.providers.anthropic import build_snapshot as build_anthropic_snapshot
from core.llm.providers.gemini import build_snapshot as build_gemini_snapshot
from core.llm.providers.ollama import build_snapshot as build_ollama_snapshot
from core.llm.providers.openai import build_snapshot as build_openai_snapshot
from core.models.schemas import ProviderName, ProviderSnapshot


def list_providers(settings: Settings) -> list[ProviderSnapshot]:
    return [
        build_ollama_snapshot(settings),
        build_anthropic_snapshot(settings),
        build_openai_snapshot(settings),
        build_gemini_snapshot(settings),
    ]


def get_provider(settings: Settings, provider_name: ProviderName) -> ProviderSnapshot:
    for provider in list_providers(settings):
        if provider.name == provider_name:
            return provider
    raise ValueError(f"Unsupported provider: {provider_name.value}")
