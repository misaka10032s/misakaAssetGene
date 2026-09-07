import logging
from functools import lru_cache
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger("misaka.config")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    misaka_env: str = Field(default="production", alias="MISAKA_ENV")
    misaka_api_host: str = Field(default="127.0.0.1", alias="MISAKA_API_HOST")
    misaka_api_port: int = Field(default=8401, alias="MISAKA_API_PORT")
    misaka_api_base: str = Field(default="http://127.0.0.1:8401", alias="MISAKA_API_BASE")
    misaka_frontend_port: int = Field(default=8400, alias="MISAKA_FRONTEND_PORT")
    misaka_network_mode: str = Field(default="auto", alias="MISAKA_NETWORK_MODE")
    # 待回答 #47 — extra Origin values accepted by core/network/origin_guard.py
    # (and mirrored into CORS) beyond the built-in loopback + Tauri defaults.
    # Comma-separated exact `scheme://host:port` strings; empty by default.
    misaka_allowed_origins: str = Field(default="", alias="MISAKA_ALLOWED_ORIGINS")
    misaka_default_locale: str = Field(default="zh-TW", alias="MISAKA_DEFAULT_LOCALE")
    misaka_ollama_base_url: str = Field(default="http://127.0.0.1:11434", alias="MISAKA_OLLAMA_BASE_URL")
    misaka_ollama_model: str = Field(default="qwen2.5:7b-instruct", alias="MISAKA_OLLAMA_MODEL")
    # Vision model used ONLY by the fidelity critic (core/llm/vision.py,
    # spec §5.15 / §3.1) — a separate key from misaka_ollama_model since a
    # text-only model cannot serve image-critique requests.
    misaka_ollama_vision_model: str = Field(default="qwen2.5vl:7b", alias="MISAKA_OLLAMA_VISION_MODEL")
    misaka_auto_start_ollama: bool = Field(default=False, alias="MISAKA_AUTO_START_OLLAMA")
    misaka_llm_provider_order: str = Field(
        default="ollama",
        alias="MISAKA_LLM_PROVIDER_ORDER",
    )
    misaka_model_dir: str = Field(default=".model", alias="MISAKA_MODEL_DIR")
    misaka_extra_model_paths: str = Field(default="", alias="MISAKA_EXTRA_MODEL_PATHS")
    # Default ComfyUI checkpoint seeded onto newly-built IMAGE jobs and used as
    # the fallback in _resolve_checkpoint_name when no explicit job/refine
    # override is present (spec §6.2 / measured 2026-09-04: without this, the
    # adapter fell back to the live checkpoint list's alphabetical first entry,
    # which landed generations on an unrelated 3D-style checkpoint).
    misaka_comfyui_default_checkpoint: str = Field(
        default="novaAnimeXL_ilV180.safetensors",
        alias="MISAKA_COMFYUI_DEFAULT_CHECKPOINT",
    )
    # Default negative prompt for every ComfyUI recipe (txt2img/img2img/inpaint)
    # when a job/refine's own ``params.negative`` is absent (spec §6.2 /
    # BP-REFINE-1). Was previously a module constant hardcoded in
    # core/generation/adapters/comfyui.py with no override path at all — moved
    # here so it is configurable like every other ComfyUI default and so a
    # job/refine can override it via the same params vocabulary. A general,
    # non-NSFW-specific quality negative; not tuned to any one checkpoint.
    misaka_comfyui_default_negative_prompt: str = Field(
        default=(
            "low quality, worst quality, blurry, jpeg artifacts, bad anatomy, "
            "extra limbs, missing limbs, extra fingers, fused fingers, mutated hands, "
            "poorly drawn face, poorly drawn hands, watermark, signature, text, logo"
        ),
        alias="MISAKA_COMFYUI_DEFAULT_NEGATIVE",
    )
    anthropic_api_key: str = Field(default="", alias="ANTHROPIC_API_KEY")
    anthropic_api_base_url: str = Field(default="https://api.anthropic.com", alias="ANTHROPIC_API_BASE_URL")
    anthropic_model: str = Field(default="claude-3-5-sonnet-latest", alias="ANTHROPIC_MODEL")
    openai_api_key: str = Field(default="", alias="OPENAI_API_KEY")
    openai_api_base_url: str = Field(default="https://api.openai.com/v1", alias="OPENAI_API_BASE_URL")
    openai_model: str = Field(default="gpt-4.1-mini", alias="OPENAI_MODEL")
    gemini_api_key: str = Field(default="", alias="GEMINI_API_KEY")
    gemini_api_base_url: str = Field(
        default="https://generativelanguage.googleapis.com/v1beta",
        alias="GEMINI_API_BASE_URL",
    )
    gemini_model: str = Field(default="gemini-2.5-flash", alias="GEMINI_MODEL")
    # Fidelity critic (core/llm/vision.py) gate config — spec §5.15 / §3.4
    # gates #4/#5, C5 fix (2026-09-06, fidelity-critic-second-opinion).
    # ``off`` disables gate #5 entirely (no second-opinion call, ever); a
    # ``fine_detail``/``unverified`` check then just stays whatever gate #4
    # left it as.
    misaka_fidelity_second_opinion: Literal["off", "gemini", "openai"] = Field(
        default="gemini", alias="MISAKA_FIDELITY_SECOND_OPINION",
    )
    # Gate #4: a "pass" verdict below this confidence is downgraded to
    # "unverified" regardless of bbox — measured default (0.7), not yet
    # tuned against a live acceptance run.
    misaka_fidelity_pass_min_confidence: float = Field(
        default=0.7, ge=0.0, le=1.0, alias="MISAKA_FIDELITY_PASS_MIN_CONFIDENCE",
    )

    @property
    def is_dev(self) -> bool:
        return self.misaka_env.lower() == "dev"

    @property
    def model_search_paths(self) -> list[str]:
        extra_paths = [
            path.strip()
            for path in self.misaka_extra_model_paths.split(";")
            if path.strip()
        ]
        ordered_paths = [self.misaka_model_dir, *extra_paths]
        normalized: list[str] = []
        for path in ordered_paths:
            resolved = str(Path(path).resolve())
            if resolved not in normalized:
                normalized.append(resolved)
        return normalized

    @property
    def llm_provider_order(self) -> list[str]:
        return [
            provider_name.strip().lower()
            for provider_name in self.misaka_llm_provider_order.split(",")
            if provider_name.strip()
        ]

    @property
    def allowed_origins_extra(self) -> list[str]:
        """Comma-split ``misaka_allowed_origins``, keeping only entries that
        parse as a concrete ``scheme://host[:port]`` origin (non-empty scheme
        + hostname, no path/query/fragment, no wildcard) — anything else
        (bare ``*``, a wildcard subdomain like ``https://*.example``, a
        scheme-less or path-carrying value) is dropped with a logged WARNING
        naming the rejected value, never silently forwarded to
        ``resolve_allowed_origins`` (core/network/origin_guard.py) and from
        there into ``CORSMiddleware``'s ``allow_origins`` — a literal ``"*"``
        there combined with this app's ``allow_credentials=True`` (core/
        main.py) turns into a credentialed CORS wildcard (待回答 #47/#48
        fresh-review F2)."""
        valid: list[str] = []
        for raw in self.misaka_allowed_origins.split(","):
            origin = raw.strip()
            if not origin:
                continue
            if "*" in origin:
                logger.warning(
                    "Dropping MISAKA_ALLOWED_ORIGINS entry %r: wildcards are not allowed.",
                    origin,
                )
                continue
            parsed = urlsplit(origin)
            if (
                not parsed.scheme
                or not parsed.hostname
                or parsed.path
                or parsed.query
                or parsed.fragment
                or parsed.username
                or parsed.password
            ):
                logger.warning(
                    "Dropping MISAKA_ALLOWED_ORIGINS entry %r: not a concrete scheme://host[:port] origin.",
                    origin,
                )
                continue
            valid.append(origin)
        return valid


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
