from core.config import Settings
from core.llm.providers.anthropic import build_snapshot as build_anthropic_snapshot
from core.llm.providers.gemini import build_snapshot as build_gemini_snapshot
from core.llm.providers.ollama import build_snapshot as build_ollama_snapshot
from core.llm.providers.openai import build_snapshot as build_openai_snapshot
from core.models.schemas import ProviderMode, ProviderName, ProviderSnapshot, ProviderStatus
from core.network.state import NetworkState


def list_providers(settings: Settings) -> list[ProviderSnapshot]:
    return [
        build_ollama_snapshot(settings),
        build_anthropic_snapshot(settings),
        build_openai_snapshot(settings),
        build_gemini_snapshot(settings),
    ]


def gate_providers(
    providers: list[ProviderSnapshot],
    state: NetworkState,
) -> list[ProviderSnapshot]:
    """Disable cloud providers that the effective network state forbids.

    Cloud providers are only usable when ``ONLINE`` (spec §11.5 offline matrix:
    "Cloud API 對話 ✗ 自動 disable"). Gated providers are marked DISABLED so the
    UI can grey them out with a lock tooltip. Local providers are untouched.
    """
    gated: list[ProviderSnapshot] = []
    for provider in providers:
        if provider.mode == ProviderMode.CLOUD and state != NetworkState.ONLINE:
            gated.append(provider.model_copy(update={"status": ProviderStatus.DISABLED}))
        else:
            gated.append(provider)
    return gated


def list_providers_for_state(
    settings: Settings,
    state: NetworkState,
) -> list[ProviderSnapshot]:
    """Provider snapshots with cloud options gated by the effective state."""
    return gate_providers(list_providers(settings), state)


def get_provider(settings: Settings, provider_name: ProviderName) -> ProviderSnapshot:
    for provider in list_providers(settings):
        if provider.name == provider_name:
            return provider
    raise ValueError(f"Unsupported provider: {provider_name.value}")
