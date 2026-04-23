from __future__ import annotations

from core.config import Settings
from core.llm.providers.anthropic import optimize_synopsis as anthropic_optimize_synopsis
from core.llm.providers.gemini import optimize_synopsis as gemini_optimize_synopsis
from core.llm.providers.ollama import optimize_synopsis as ollama_optimize_synopsis
from core.llm.providers.openai import optimize_synopsis as openai_optimize_synopsis
from core.models.schemas import ProviderStatus, SynopsisOptimizeResult


def build_synopsis_prompt(project_name: str, project_type: str, synopsis: str) -> str:
    return (
        "Please refine the following project synopsis for a multimodal asset generation workspace.\n"
        "Keep the response in the same language as the original synopsis.\n"
        "If the original synopsis is written in Chinese, return Chinese.\n"
        "If the original synopsis is written in Japanese, return Japanese.\n"
        "If the original synopsis is written in English, return English.\n"
        "Do not switch the language.\n"
        "Return only the improved synopsis as one concise paragraph.\n"
        "Clarify the setting, tone, protagonist goal, and the intended asset style.\n\n"
        f"Project name: {project_name}\n"
        f"Project type: {project_type}\n"
        f"Original synopsis: {synopsis}"
    )


def optimize_synopsis(settings: Settings, project_name: str, project_type: str, synopsis: str) -> SynopsisOptimizeResult:
    prompt = build_synopsis_prompt(project_name, project_type, synopsis)
    provider_handlers = {
        "ollama": lambda: ollama_optimize_synopsis(settings, prompt),
        "anthropic": lambda: anthropic_optimize_synopsis(settings, prompt),
        "claude": lambda: anthropic_optimize_synopsis(settings, prompt),
        "openai": lambda: openai_optimize_synopsis(settings, prompt),
        "chatgpt": lambda: openai_optimize_synopsis(settings, prompt),
        "gemini": lambda: gemini_optimize_synopsis(settings, prompt),
    }

    for provider_name in settings.llm_provider_order:
        provider_handler = provider_handlers.get(provider_name)
        if provider_handler is None:
            continue
        provider_result = provider_handler()
        if provider_result is not None:
            return provider_result

    return SynopsisOptimizeResult(
        optimized_synopsis=synopsis.strip(),
        strategy=ProviderStatus.DISABLED.value,
        provider=None,
    )
