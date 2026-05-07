from __future__ import annotations

import re

from fastapi import HTTPException

from core.config import Settings
from core.llm.providers.anthropic import optimize_synopsis as anthropic_optimize_synopsis
from core.llm.providers.gemini import optimize_synopsis as gemini_optimize_synopsis
from core.llm.providers.ollama import optimize_synopsis as ollama_optimize_synopsis
from core.llm.providers.openai import optimize_synopsis as openai_optimize_synopsis
from core.models.schemas import ProviderStatus, SynopsisOptimizeResult


_HIRAGANA_KATAKANA_RE = re.compile(r"[\u3040-\u30ff]")
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
_WORD_RE = re.compile(r"[a-zA-Z][a-zA-Z0-9'-]{2,}")


def build_synopsis_prompt(project_name: str, project_type: str, synopsis: str) -> str:
    return (
        "You are refining a project synopsis for a multimodal production workspace.\n"
        "Keep the response in the same language as the original synopsis.\n"
        "Do not switch the language.\n"
        "Do not invent any new facts, names, places, events, lore, relationships, or technologies that are not present in the original synopsis.\n"
        "Do not add assumptions such as country names, disasters, or character relationships unless they are explicitly stated.\n"
        "Improve the synopsis using these catalysts:\n"
        "1. clarify the core subject and intended output direction\n"
        "2. make the creative scope easier to understand for later image, voice, music, and video production\n"
        "3. preserve tone and genre cues from the original text\n"
        "4. remove vague repetition and awkward phrasing\n"
        "5. keep it concise, production-oriented, and easy to use during project setup\n"
        "Return only the improved synopsis as one paragraph.\n\n"
        f"Project name: {project_name}\n"
        f"Project type: {project_type}\n"
        f"Original synopsis: {synopsis}"
    )


def _detect_language(text: str) -> str:
    if _HIRAGANA_KATAKANA_RE.search(text):
        return "ja"
    if _CJK_RE.search(text):
        return "zh"
    return "en"


def _sanitize_synopsis_text(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("\r", " ").replace("\n", " ")).strip()


def _fallback_synopsis(project_name: str, project_type: str, synopsis: str) -> str:
    clean_synopsis = synopsis.strip().rstrip("。.!? ")
    language = _detect_language(clean_synopsis)
    if language == "zh":
        return (
            f"《{project_name}》聚焦於{clean_synopsis}，整體方向延續原本溫度與核心主題，"
            "並整理成更適合後續圖文影音製作與專案規劃使用的概要。"
        )
    if language == "ja":
        return (
            f"『{project_name}』は、{clean_synopsis}を軸にした企画です。"
            "元のトーンと主題を保ちながら、今後のマルチモーダル素材制作につなげやすい概要として整理しています。"
        )
    return (
        f'"{project_name}" centers on {clean_synopsis}. '
        "The revised synopsis keeps the original tone and intent while making the project direction clearer for later multimodal production planning."
    )


def _has_language_drift(source: str, candidate: str) -> bool:
    return _detect_language(source) != _detect_language(candidate)


def _has_untrusted_format(candidate: str) -> bool:
    lowered = candidate.lower()
    return any(
        marker in lowered
        for marker in (
            "improved synopsis",
            "project name:",
            "improved purpose",
            "项目名称",
            "项目类型",
            "原稿标题",
            "原始情节",
            "改进",
            "改善后",
        )
    )


def _has_excessive_growth(source: str, candidate: str) -> bool:
    return len(candidate) > max(len(source) * 2, len(source) + 120)


def _has_too_many_new_words(project_name: str, project_type: str, source: str, candidate: str) -> bool:
    if _detect_language(source) != "en":
        return False
    allowed_words = {
        word.lower()
        for word in _WORD_RE.findall(f"{project_name} {project_type} {source}")
    }
    candidate_words = {word.lower() for word in _WORD_RE.findall(candidate)}
    new_words = {word for word in candidate_words - allowed_words if len(word) >= 4}
    return len(new_words) > 4


def _finalize_synopsis(
    project_name: str,
    project_type: str,
    source: str,
    result: SynopsisOptimizeResult,
) -> SynopsisOptimizeResult:
    optimized_synopsis = _sanitize_synopsis_text(result.optimized_synopsis)
    if (
        not optimized_synopsis
        or _has_language_drift(source, optimized_synopsis)
        or _has_untrusted_format(optimized_synopsis)
        or _has_excessive_growth(source, optimized_synopsis)
        or _has_too_many_new_words(project_name, project_type, source, optimized_synopsis)
    ):
        return SynopsisOptimizeResult(
            optimized_synopsis=_fallback_synopsis(project_name, project_type, source),
            strategy=f"{result.strategy}_guarded_fallback",
            provider=result.provider,
        )
    result.optimized_synopsis = optimized_synopsis
    return result


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
            return _finalize_synopsis(project_name, project_type, synopsis, provider_result)

    raise HTTPException(
        status_code=409,
        detail="No ready synopsis optimization provider is available. Configure a cloud API key or install at least one Ollama model.",
    )
