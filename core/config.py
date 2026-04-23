from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    misaka_env: str = Field(default="production", alias="MISAKA_ENV")
    misaka_api_host: str = Field(default="127.0.0.1", alias="MISAKA_API_HOST")
    misaka_api_port: int = Field(default=7500, alias="MISAKA_API_PORT")
    misaka_api_base: str = Field(default="http://127.0.0.1:7500", alias="MISAKA_API_BASE")
    misaka_frontend_port: int = Field(default=7501, alias="MISAKA_FRONTEND_PORT")
    misaka_cors_origin_regex: str = Field(
        default=r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$",
        alias="MISAKA_CORS_ORIGIN_REGEX",
    )
    misaka_default_locale: str = Field(default="zh-TW", alias="MISAKA_DEFAULT_LOCALE")
    misaka_ollama_base_url: str = Field(default="http://127.0.0.1:11434", alias="MISAKA_OLLAMA_BASE_URL")
    misaka_ollama_model: str = Field(default="qwen2.5:7b-instruct", alias="MISAKA_OLLAMA_MODEL")
    misaka_auto_start_ollama: bool = Field(default=False, alias="MISAKA_AUTO_START_OLLAMA")
    misaka_llm_provider_order: str = Field(
        default="ollama",
        alias="MISAKA_LLM_PROVIDER_ORDER",
    )
    misaka_model_dir: str = Field(default=".model", alias="MISAKA_MODEL_DIR")
    misaka_extra_model_paths: str = Field(default="", alias="MISAKA_EXTRA_MODEL_PATHS")
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


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
