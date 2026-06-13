"""Setup-error AI explanation flow (spec §11.3).

When the user chooses [y] at the unknown-error prompt this module:
1. Reads the root ``.env`` to discover any configured API key or Ollama URL.
2. If NONE configured → returns guidance text (provider links) without making
   any network call.
3. If a key/local-LLM is found → builds a prompt from the last 50 lines of
   ``setup.log`` + current system info, then calls the LLM via an injectable
   client and returns the explanation text.

Security rules (enforced here):
- API keys are read from ``.env`` only; they are NEVER logged, echoed, or
  placed in URL query parameters.
- The LLM client receives the key through its constructor / auth header only.
- Log lines are passed through ``_redact`` from setup_diagnostics before being
  embedded in the prompt (belt-and-suspenders — the log itself is already
  redacted at write time, but re-redact for safety).

Injectable client contract:
    The ``llm_client`` argument (default ``None`` → auto-detect) must be a
    callable with signature::

        def llm_client(prompt: str) -> str: ...

    Tests inject a fake that never touches the network.  Production uses
    ``_build_default_client`` which reads ``.env`` and wires up Ollama first,
    then cloud providers as fallback.

Design is recorded in .plan/RESEARCH_LOG.md §8.
"""

from __future__ import annotations

import os
import platform
from pathlib import Path
from typing import Callable, Optional

from scripts.lib.setup_diagnostics import _redact


# ---------------------------------------------------------------------------
# Provider links shown when no key is configured
# ---------------------------------------------------------------------------

NO_KEY_GUIDANCE = """\
目前未設定任何 API Key 或本機 Ollama。

若要使用 AI 解釋功能，請先取得一組免費 API Key（以下為常見 provider）：
  • Anthropic (Claude)  → https://console.anthropic.com/
  • OpenAI (GPT-4)      → https://platform.openai.com/
  • Google (Gemini)     → https://aistudio.google.com/apikey
  • OpenRouter (多 provider 免費額度) → https://openrouter.ai/

取得 Key 後，請將其填入專案根目錄的 .env 檔，例如：
  ANTHROPIC_API_KEY=sk-ant-...

若要使用完全離線的本機 AI，請安裝 Ollama：
  https://ollama.ai/
"""

_PROVIDER_LINKS = {
    "anthropic": "https://console.anthropic.com/",
    "openai": "https://platform.openai.com/",
    "gemini": "https://aistudio.google.com/apikey",
    "openrouter": "https://openrouter.ai/",
    "ollama": "https://ollama.ai/",
}


# ---------------------------------------------------------------------------
# Detect configured providers from .env (read-only; never written/logged)
# ---------------------------------------------------------------------------

def _load_env_keys(root: Path) -> dict[str, str]:
    """Parse root .env for known API-key / URL variables.

    Returns a dict of ``env_var_name → value`` for non-empty keys.
    NEVER logs or prints any of these values.
    """
    env_file = root / ".env"
    result: dict[str, str] = {}
    watched = {
        "ANTHROPIC_API_KEY",
        "OPENAI_API_KEY",
        "GEMINI_API_KEY",
        "MISAKA_OLLAMA_BASE_URL",
    }
    if not env_file.exists():
        return result
    try:
        for raw_line in env_file.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            var, _, val = line.partition("=")
            var = var.strip()
            val = val.strip().strip('"').strip("'")
            if var in watched and val:
                result[var] = val
    except OSError:
        pass
    return result


def _has_any_provider(env_keys: dict[str, str]) -> bool:
    cloud_keys = {"ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY"}
    if any(k in env_keys for k in cloud_keys):
        return True
    # Ollama: key may exist and be non-empty (a custom URL), or we check
    # the default 127.0.0.1:11434 — treat as available if explicitly set
    if "MISAKA_OLLAMA_BASE_URL" in env_keys:
        return True
    return False


# ---------------------------------------------------------------------------
# Default LLM client builder (production path)
# ---------------------------------------------------------------------------

def _build_ollama_client(base_url: str, model: str) -> Callable[[str], str]:
    """Return a simple callable that sends a prompt to a local Ollama instance."""
    import httpx  # imported lazily — not available at import time in bare Python

    def call(prompt: str) -> str:
        # Key never used here; Ollama is unauthenticated (local only)
        resp = httpx.post(
            f"{base_url.rstrip('/')}/api/generate",
            timeout=60.0,
            json={
                "model": model,
                "prompt": prompt,
                "stream": False,
                "options": {"temperature": 0.3, "num_predict": 400},
            },
        )
        resp.raise_for_status()
        return str(resp.json().get("response") or "").strip()

    return call


def _build_anthropic_client(api_key: str, model: str, base_url: str) -> Callable[[str], str]:
    """Return a callable that calls the Anthropic Messages API.

    The ``api_key`` is passed via the ``x-api-key`` header only.
    It is NEVER placed in a URL, logged, or echoed.
    """
    import httpx

    def call(prompt: str) -> str:
        resp = httpx.post(
            f"{base_url.rstrip('/')}/v1/messages",
            timeout=60.0,
            headers={
                "x-api-key": api_key,          # key via header only
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": model,
                "max_tokens": 400,
                "messages": [{"role": "user", "content": prompt}],
            },
        )
        resp.raise_for_status()
        blocks = resp.json().get("content") or []
        return str(blocks[0].get("text") or "").strip() if blocks else ""

    return call


def _build_openai_client(api_key: str, model: str, base_url: str) -> Callable[[str], str]:
    """Return a callable that calls the OpenAI Chat Completions API.

    ``api_key`` is sent via the ``Authorization: Bearer`` header only.
    """
    import httpx

    def call(prompt: str) -> str:
        resp = httpx.post(
            f"{base_url.rstrip('/')}/chat/completions",
            timeout=60.0,
            headers={
                "Authorization": f"Bearer {api_key}",  # key via header only
                "content-type": "application/json",
            },
            json={
                "model": model,
                "max_tokens": 400,
                "messages": [{"role": "user", "content": prompt}],
            },
        )
        resp.raise_for_status()
        choices = resp.json().get("choices") or []
        return str((choices[0].get("message") or {}).get("content") or "").strip() if choices else ""

    return call


def _build_gemini_client(api_key: str, model: str) -> Callable[[str], str]:
    """Return a callable that calls the Gemini generateContent API.

    ``api_key`` is passed via the ``x-goog-api-key`` header only.
    It is NEVER placed in a URL query parameter, logged, or echoed.
    """
    import httpx

    # Gemini REST endpoint — key goes in header, never in URL query param.
    base_url = "https://generativelanguage.googleapis.com/v1beta"

    def call(prompt: str) -> str:
        resp = httpx.post(
            f"{base_url}/models/{model}:generateContent",
            timeout=60.0,
            headers={
                "x-goog-api-key": api_key,   # key via header only (security)
                "content-type": "application/json",
            },
            json={
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {"maxOutputTokens": 400, "temperature": 0.3},
            },
        )
        resp.raise_for_status()
        candidates = resp.json().get("candidates") or []
        if candidates:
            parts = (candidates[0].get("content") or {}).get("parts") or []
            return str(parts[0].get("text") or "").strip() if parts else ""
        return ""

    return call


def _build_default_client(
    env_keys: dict[str, str],
) -> Optional[Callable[[str], str]]:
    """Auto-detect and build a client from env keys.

    Priority: Ollama (local) first, then Anthropic, then OpenAI, then Gemini.
    Returns ``None`` if nothing is usable.
    """
    # 1. Try Ollama
    ollama_url = env_keys.get("MISAKA_OLLAMA_BASE_URL", "")
    if ollama_url:
        model = os.environ.get("MISAKA_OLLAMA_MODEL", "qwen2.5:7b-instruct")
        return _build_ollama_client(ollama_url, model)

    # 2. Anthropic
    anthropic_key = env_keys.get("ANTHROPIC_API_KEY", "")
    if anthropic_key:
        model = os.environ.get("ANTHROPIC_MODEL", "claude-3-5-sonnet-latest")
        base = os.environ.get("ANTHROPIC_API_BASE_URL", "https://api.anthropic.com")
        return _build_anthropic_client(anthropic_key, model, base)

    # 3. OpenAI
    openai_key = env_keys.get("OPENAI_API_KEY", "")
    if openai_key:
        model = os.environ.get("OPENAI_MODEL", "gpt-4.1-mini")
        base = os.environ.get("OPENAI_API_BASE_URL", "https://api.openai.com/v1")
        return _build_openai_client(openai_key, model, base)

    # 4. Gemini
    gemini_key = env_keys.get("GEMINI_API_KEY", "")
    if gemini_key:
        model = os.environ.get("GEMINI_MODEL", "gemini-2.0-flash")
        return _build_gemini_client(gemini_key, model)

    return None


# ---------------------------------------------------------------------------
# Prompt builder
# ---------------------------------------------------------------------------

def _read_last_n_lines(log_path: Path, n: int = 50) -> str:
    """Read and return the last ``n`` lines of ``log_path``, redacted."""
    if not log_path.exists():
        return "(setup.log not found)"
    try:
        lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
        tail = lines[-n:] if len(lines) >= n else lines
        return _redact("\n".join(tail))
    except OSError:
        return "(could not read setup.log)"


def build_explain_prompt(log_path: Path, stage_label: str) -> str:
    """Build the LLM prompt: last 50 log lines + system info."""
    sysinfo = (
        f"OS: {platform.system()} {platform.release()} {platform.machine()}\n"
        f"Python: {platform.python_version()}"
    )
    log_tail = _read_last_n_lines(log_path, n=50)
    return (
        "You are a helpful technical assistant diagnosing a software setup error.\n"
        "The user is running a desktop AI tool called MisakaAssetGene.\n"
        f"Setup stage that failed: {stage_label}\n\n"
        f"System info:\n{sysinfo}\n\n"
        f"Last 50 lines of setup.log:\n```\n{log_tail}\n```\n\n"
        "Please explain what likely caused this error and what the user should do "
        "to fix it. Be concise (≤200 words). Use Traditional Chinese (zh-TW)."
    )


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def explain_setup_error(
    stage_label: str,
    log_path: Path,
    root: Optional[Path] = None,
    llm_client: Optional[Callable[[str], str]] = None,
) -> str:
    """Return an AI explanation string for the setup error.

    Args:
        stage_label: Human-readable name of the failed stage (e.g. ``"[5/7] 安裝核心依賴"``).
        log_path: Path to ``setup.log``.
        root: Repo root (used to locate ``.env``).  Defaults to ``Path.cwd()``.
        llm_client: Injectable callable ``(prompt: str) -> str``.  Pass a fake
            in tests.  ``None`` → auto-detect from ``.env``.

    Returns:
        Explanation text (str).  Never raises — returns an error/guidance string
        on failure so the caller can always print something useful.

    Security: API keys are read from ``.env`` but NEVER included in any
    returned string, log entry, or error message.
    """
    repo_root = root or Path.cwd()
    env_keys = _load_env_keys(repo_root)

    # Determine the client to use
    if llm_client is None:
        if not _has_any_provider(env_keys):
            return NO_KEY_GUIDANCE
        llm_client = _build_default_client(env_keys)
        if llm_client is None:
            return NO_KEY_GUIDANCE

    # Build prompt and call
    prompt = build_explain_prompt(log_path, stage_label)
    try:
        explanation = llm_client(prompt)
        if not explanation:
            return "AI 未回傳解釋內容。請重試，或查看 setup.log 以取得原始錯誤資訊。"
        return explanation
    except Exception as exc:  # noqa: BLE001
        # Log nothing about the key — exc may contain network details but not keys
        # (keys are never in URLs; they're in headers which don't appear in httpx errors)
        return f"AI 解釋請求失敗：{type(exc).__name__}。請查看 setup.log 以取得原始錯誤資訊。"
