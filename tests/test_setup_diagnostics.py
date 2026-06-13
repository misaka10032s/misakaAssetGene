"""Tests for setup diagnostics and AI explanation flow (spec §11.3, M4.e).

Coverage:
  (a) Whitelist classification — each known-error key → correct key + remediation.
  (b) Unknown error → setup.log written + correct console summary shape.
  (c) AI-explanation flow with FAKE LLM client:
        • no-key path → returns guidance (no call)
        • key-present path → builds prompt from last-50 lines + sysinfo + returns fake explanation
  (d) API key MUST NOT appear in setup.log or console summary.

No real network calls.  No real subprocess.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from scripts.lib.setup_diagnostics import (
    KNOWN_ERRORS,
    ClassifyResult,
    build_console_summary,
    classify_error,
    write_to_log,
    _redact,
)
from scripts.lib.setup_ai_explain import (
    NO_KEY_GUIDANCE,
    _load_env_keys,
    _has_any_provider,
    _build_default_client,
    build_explain_prompt,
    explain_setup_error,
)


# ============================================================================
# (a) Whitelist classification
# ============================================================================


class TestClassifyError:
    """Each known-error key must be matched by at least one pattern."""

    def test_network_timeout(self):
        result = classify_error("Connection timed out after 30s")
        assert result.matched is True
        assert result.key == "network_timeout"
        assert result.friendly_message is not None
        assert len(result.friendly_message) > 0

    def test_network_timeout_variant(self):
        result = classify_error("urllib.error.URLError: <urlopen error timed out>")
        assert result.matched is True
        assert result.key == "network_timeout"

    def test_network_unreachable(self):
        result = classify_error("ConnectionRefusedError: [WinError 10061]")
        assert result.matched is True
        assert result.key == "network_unreachable"

    def test_network_unreachable_getaddrinfo(self):
        result = classify_error("socket.gaierror: [Errno 11001] getaddrinfo failed")
        assert result.matched is True
        assert result.key == "network_unreachable"

    def test_disk_full(self):
        result = classify_error("OSError: [Errno 28] No space left on device")
        assert result.matched is True
        assert result.key == "disk_full"

    def test_disk_full_windows(self):
        result = classify_error("WinError 112: There is not enough space on the disk.")
        assert result.matched is True
        assert result.key == "disk_full"

    def test_cuda_missing_libcuda(self):
        result = classify_error("ImportError: libcuda.so.1 not found")
        assert result.matched is True
        assert result.key == "cuda_missing"

    def test_cuda_missing_driver(self):
        result = classify_error("NVIDIA-SMI has failed because it couldn't communicate")
        assert result.matched is True
        assert result.key == "cuda_missing"

    def test_powershell_execution_policy(self):
        result = classify_error(
            "File setup.ps1 cannot be loaded because running scripts is disabled"
        )
        assert result.matched is True
        assert result.key == "powershell_execution_policy"
        assert result.remediation_cmd is not None

    def test_hash_mismatch(self):
        result = classify_error("sha256 digest did not match expected value")
        assert result.matched is True
        assert result.key == "hash_mismatch"

    def test_permission_denied(self):
        result = classify_error("PermissionError: [Errno 13] Permission denied: '/usr/local/bin/uv'")
        assert result.matched is True
        assert result.key == "permission_denied"

    def test_unknown_error_returns_not_matched(self):
        result = classify_error("Some completely unexpected internal error XYZ123")
        assert result.matched is False
        assert result.key is None
        assert result.friendly_message is None

    def test_all_whitelist_keys_have_unique_keys(self):
        keys = [e.key for e in KNOWN_ERRORS]
        assert len(keys) == len(set(keys)), "Duplicate whitelist keys found"

    def test_all_whitelist_entries_have_patterns(self):
        for entry in KNOWN_ERRORS:
            assert len(entry.patterns) >= 1, f"{entry.key} has no patterns"

    def test_classify_is_case_insensitive(self):
        result = classify_error("CONNECTION TIMED OUT")
        assert result.matched is True
        assert result.key == "network_timeout"


# ============================================================================
# (b) Unknown error → setup.log written + correct console summary shape
# ============================================================================


class TestUnknownErrorLogging:
    def test_write_to_log_creates_file(self, tmp_path: Path):
        log_path = write_to_log(
            stage_label="[5/7] 安裝核心依賴",
            error_text="Unexpected failure in dependency resolution",
            exc=None,
            root=tmp_path,
        )
        assert log_path.exists()
        content = log_path.read_text(encoding="utf-8")
        assert "[5/7] 安裝核心依賴" in content
        assert "Unexpected failure" in content

    def test_write_to_log_with_exception(self, tmp_path: Path):
        try:
            raise RuntimeError("test exception detail")
        except RuntimeError as exc:
            log_path = write_to_log(
                stage_label="[3/7] 建立虛擬環境",
                error_text="RuntimeError: test exception detail",
                exc=exc,
                root=tmp_path,
            )
        content = log_path.read_text(encoding="utf-8")
        assert "RuntimeError" in content
        assert "test exception detail" in content

    def test_write_to_log_multiple_entries_appends(self, tmp_path: Path):
        for i in range(3):
            write_to_log(
                stage_label=f"[{i+1}/7] stage",
                error_text=f"Error #{i}",
                exc=None,
                root=tmp_path,
            )
        content = (tmp_path / "setup.log").read_text(encoding="utf-8")
        assert "Error #0" in content
        assert "Error #1" in content
        assert "Error #2" in content

    def test_console_summary_shape(self, tmp_path: Path):
        log_path = tmp_path / "setup.log"
        log_path.write_text("(log content)", encoding="utf-8")

        summary = build_console_summary(
            stage_index=5,
            stage_total=7,
            one_line_summary="ImportError: libcuda.so.1 not found",
            log_path=log_path,
        )
        assert "步驟 [5/7]" in summary
        assert "ImportError: libcuda.so.1 not found" in summary
        assert "setup.log" in summary
        assert "[y]" in summary
        assert "[n]" in summary
        assert "[s]" in summary

    def test_console_summary_contains_full_log_path(self, tmp_path: Path):
        log_path = tmp_path / "setup.log"
        log_path.write_text("x", encoding="utf-8")
        summary = build_console_summary(5, 7, "err", log_path)
        assert str(log_path) in summary


# ============================================================================
# (c) AI explanation flow — fake LLM client
# ============================================================================


class TestAIExplainFlow:
    """All tests use a fake llm_client; no real network is touched."""

    FAKE_KEY = "sk-ant-TESTKEY1234567890ABCDEF"  # fake, for testing only

    def _make_env(self, tmp_path: Path, content: str) -> None:
        (tmp_path / ".env").write_text(content, encoding="utf-8")

    def _make_log(self, tmp_path: Path, lines: int = 60) -> Path:
        log_path = tmp_path / "setup.log"
        log_lines = [f"log line {i}" for i in range(lines)]
        log_path.write_text("\n".join(log_lines), encoding="utf-8")
        return log_path

    # -- no-key path: returns guidance without calling client ----------------

    def test_no_key_returns_guidance_without_calling_client(self, tmp_path: Path):
        call_count = {"n": 0}

        def fake_client(prompt: str) -> str:
            call_count["n"] += 1
            return "should not be called"

        # no .env at all
        log_path = self._make_log(tmp_path)
        result = explain_setup_error(
            stage_label="[5/7] 安裝核心依賴",
            log_path=log_path,
            root=tmp_path,
            llm_client=None,  # auto-detect; no .env → no provider
        )
        # guidance returned (not the fake client's return)
        assert call_count["n"] == 0
        assert "API Key" in result or "Ollama" in result or "provider" in result.lower() or "https://" in result

    def test_no_key_guidance_mentions_providers(self, tmp_path: Path):
        log_path = self._make_log(tmp_path)
        result = explain_setup_error("[1/7]", log_path, root=tmp_path, llm_client=None)
        assert "anthropic" in result.lower() or "openai" in result.lower() or "https://" in result

    # -- key-present path: calls the injected fake client -------------------

    def test_key_present_calls_fake_client(self, tmp_path: Path):
        self._make_env(tmp_path, f"ANTHROPIC_API_KEY={self.FAKE_KEY}")
        log_path = self._make_log(tmp_path)

        call_args: list[str] = []

        def fake_client(prompt: str) -> str:
            call_args.append(prompt)
            return "AI 解釋：這是一個測試回應。"

        result = explain_setup_error(
            stage_label="[5/7] 安裝核心依賴",
            log_path=log_path,
            root=tmp_path,
            llm_client=fake_client,
        )
        assert result == "AI 解釋：這是一個測試回應。"
        assert len(call_args) == 1

    def test_prompt_contains_last_50_log_lines(self, tmp_path: Path):
        log_path = self._make_log(tmp_path, lines=80)  # 80 lines, only last 50 should appear
        captured: list[str] = []

        def fake_client(prompt: str) -> str:
            captured.append(prompt)
            return "ok"

        explain_setup_error("[3/7]", log_path, root=tmp_path, llm_client=fake_client)
        assert len(captured) == 1
        prompt = captured[0]
        # Line 79 should be present (last line of 80), line 0 should not
        assert "log line 79" in prompt
        assert "log line 0" not in prompt

    def test_prompt_contains_sysinfo(self, tmp_path: Path):
        import platform

        log_path = self._make_log(tmp_path)
        captured: list[str] = []

        def fake_client(prompt: str) -> str:
            captured.append(prompt)
            return "ok"

        explain_setup_error("[2/7]", log_path, root=tmp_path, llm_client=fake_client)
        prompt = captured[0]
        # Should contain OS info
        assert platform.system() in prompt

    def test_prompt_contains_stage_label(self, tmp_path: Path):
        log_path = self._make_log(tmp_path)
        captured: list[str] = []

        def fake_client(prompt: str) -> str:
            captured.append(prompt)
            return "ok"

        explain_setup_error("[6/7] 下載 ffmpeg", log_path, root=tmp_path, llm_client=fake_client)
        assert "[6/7] 下載 ffmpeg" in captured[0]

    def test_empty_response_returns_fallback_message(self, tmp_path: Path):
        log_path = self._make_log(tmp_path)

        def fake_client(prompt: str) -> str:
            return ""

        result = explain_setup_error("[1/7]", log_path, root=tmp_path, llm_client=fake_client)
        assert "setup.log" in result or "重試" in result

    def test_client_exception_returns_error_string_not_raise(self, tmp_path: Path):
        log_path = self._make_log(tmp_path)

        def fake_client(prompt: str) -> str:
            raise ConnectionError("simulated network error")

        result = explain_setup_error("[4/7]", log_path, root=tmp_path, llm_client=fake_client)
        # Should return a human-readable error message, not raise
        assert isinstance(result, str)
        assert len(result) > 0

    # -- load_env_keys and has_any_provider ----------------------------------

    def test_load_env_keys_reads_known_keys(self, tmp_path: Path):
        self._make_env(
            tmp_path,
            "ANTHROPIC_API_KEY=sk-ant-TESTXXX\nOPENAI_API_KEY=sk-openai-YYY\n",
        )
        keys = _load_env_keys(tmp_path)
        assert "ANTHROPIC_API_KEY" in keys
        assert "OPENAI_API_KEY" in keys

    def test_load_env_keys_missing_env_returns_empty(self, tmp_path: Path):
        keys = _load_env_keys(tmp_path)
        assert keys == {}

    def test_has_any_provider_true_for_anthropic(self, tmp_path: Path):
        self._make_env(tmp_path, f"ANTHROPIC_API_KEY={self.FAKE_KEY}")
        keys = _load_env_keys(tmp_path)
        assert _has_any_provider(keys) is True

    def test_has_any_provider_true_for_ollama(self, tmp_path: Path):
        self._make_env(tmp_path, "MISAKA_OLLAMA_BASE_URL=http://127.0.0.1:11434")
        keys = _load_env_keys(tmp_path)
        assert _has_any_provider(keys) is True

    def test_has_any_provider_false_when_no_keys(self, tmp_path: Path):
        keys = _load_env_keys(tmp_path)
        assert _has_any_provider(keys) is False


# ============================================================================
# (d) API key MUST NOT appear in setup.log or console summary
# ============================================================================


class TestSecurityNoKeyLeak:
    """Security: API key values must never appear in log files or console text."""

    FAKE_KEY = "sk-ant-api03-SUPERSECRET0000000000000000ABC"

    def test_api_key_not_in_setup_log(self, tmp_path: Path):
        """Even if an error message accidentally contains a key-like string, it
        must be redacted before hitting setup.log."""
        # Simulate an error that embeds a key-like string
        error_with_key = f"Authentication failed for key={self.FAKE_KEY}"
        log_path = write_to_log(
            stage_label="[5/7]",
            error_text=error_with_key,
            exc=None,
            root=tmp_path,
        )
        content = log_path.read_text(encoding="utf-8")
        # The exact key value must NOT be present
        assert self.FAKE_KEY not in content
        # But REDACTED marker should be present
        assert "[REDACTED]" in content

    def test_api_key_not_in_console_summary(self, tmp_path: Path):
        """Console summary must also redact key-like strings in the one-line summary."""
        log_path = tmp_path / "setup.log"
        log_path.write_text("log", encoding="utf-8")
        key_in_summary = f"Auth error: {self.FAKE_KEY}"
        summary = build_console_summary(5, 7, key_in_summary, log_path)
        assert self.FAKE_KEY not in summary
        assert "[REDACTED]" in summary

    def test_redact_strips_bearer_token(self):
        text = "Authorization: Bearer sk-ant-SUPERSECRET0000000000000ABC"
        redacted = _redact(text)
        assert "SUPERSECRET" not in redacted
        assert "[REDACTED]" in redacted

    def test_redact_preserves_normal_text(self):
        text = "Error: file not found at /usr/local/bin/uv"
        redacted = _redact(text)
        # Short strings like paths should not be redacted (no 20+ char alphanumeric blobs)
        assert "/usr/local/bin/uv" in redacted

    def test_exception_traceback_with_key_is_redacted(self, tmp_path: Path):
        """If an exception somehow carries a key value, the traceback must be redacted."""
        key = self.FAKE_KEY
        try:
            raise ValueError(f"Bad config: api_key={key}")
        except ValueError as exc:
            log_path = write_to_log(
                stage_label="[2/7]",
                error_text=str(exc),
                exc=exc,
                root=tmp_path,
            )
        content = log_path.read_text(encoding="utf-8")
        assert key not in content

    def test_ai_explain_prompt_does_not_embed_raw_key(self, tmp_path: Path):
        """The prompt built for the LLM must not embed raw API keys (log is already redacted)."""
        log_path = tmp_path / "setup.log"
        # Simulate a log file that already has a fake key-like string
        log_path.write_text(
            f"Error: token={self.FAKE_KEY}\nother lines\n", encoding="utf-8"
        )
        prompt = build_explain_prompt(log_path, "[5/7]")
        assert self.FAKE_KEY not in prompt
        assert "[REDACTED]" in prompt


# ============================================================================
# (e) Gemini provider branch — Fix 2 (M4.e polish)
# ============================================================================


class TestGeminiProviderBranch:
    """Verify that _build_default_client selects the Gemini branch when only
    GEMINI_API_KEY is set, and that the key never appears in prompt or log."""

    FAKE_GEMINI_KEY = "AIzaFAKEGEMINIKEY00000000000000000000"

    def _make_env(self, tmp_path: Path, content: str) -> None:
        (tmp_path / ".env").write_text(content, encoding="utf-8")

    def _make_log(self, tmp_path: Path) -> Path:
        log_path = tmp_path / "setup.log"
        log_path.write_text("log line 1\nlog line 2\n", encoding="utf-8")
        return log_path

    def test_gemini_branch_selected_when_only_gemini_key_set(self, tmp_path: Path):
        """_build_default_client must return a non-None callable when only GEMINI_API_KEY is set."""
        self._make_env(tmp_path, f"GEMINI_API_KEY={self.FAKE_GEMINI_KEY}")
        keys = _load_env_keys(tmp_path)
        assert "GEMINI_API_KEY" in keys
        # Should be recognized as a valid provider
        assert _has_any_provider(keys) is True
        # Client builder must return a callable (not None)
        client = _build_default_client(keys)
        assert client is not None
        assert callable(client)

    def test_gemini_only_user_gets_client_not_no_key_guidance(self, tmp_path: Path):
        """A Gemini-only user must NOT fall through to NO_KEY_GUIDANCE.

        We inject a fake client to avoid any real network call. The injected
        client is used instead of the auto-built one to keep the test isolated;
        the preceding test already verifies _build_default_client returns
        non-None for GEMINI_API_KEY.
        """
        self._make_env(tmp_path, f"GEMINI_API_KEY={self.FAKE_GEMINI_KEY}")
        log_path = self._make_log(tmp_path)

        def fake_gemini_client(prompt: str) -> str:
            return "Gemini fake explanation"

        result = explain_setup_error(
            stage_label="[3/7] test",
            log_path=log_path,
            root=tmp_path,
            llm_client=fake_gemini_client,
        )
        # Must get the fake explanation, NOT the no-key guidance
        assert result == "Gemini fake explanation"
        assert "API Key" not in result or "Gemini fake" in result

    def test_gemini_key_not_in_prompt(self, tmp_path: Path):
        """The Gemini key must not appear in the prompt passed to the LLM."""
        log_path = tmp_path / "setup.log"
        # Simulate a log that happens to contain a key-like string
        log_path.write_text(
            f"Error: config key={self.FAKE_GEMINI_KEY}\nother content\n",
            encoding="utf-8",
        )
        prompt = build_explain_prompt(log_path, "[2/7]")
        assert self.FAKE_GEMINI_KEY not in prompt
        assert "[REDACTED]" in prompt

    def test_gemini_key_not_in_setup_log(self, tmp_path: Path):
        """If error text contains a Gemini-key-shaped string, it must be redacted in setup.log."""
        error_with_key = f"Auth failed: key={self.FAKE_GEMINI_KEY}"
        log_path = write_to_log(
            stage_label="[4/7]",
            error_text=error_with_key,
            exc=None,
            root=tmp_path,
        )
        content = log_path.read_text(encoding="utf-8")
        assert self.FAKE_GEMINI_KEY not in content
        assert "[REDACTED]" in content


# ============================================================================
# (f) Shell fallback redaction — Fix 1 (M4.e polish)
# Verifies that the Python helper (_redact / write_to_log / build_console_summary)
# that the shell fallback delegates to produces redacted output.
# ============================================================================


class TestShellFallbackRedaction:
    """The inline Python redaction path used by the shell fallback must strip keys."""

    FAKE_KEY = "sk-ant-SHELLTEST00000000000000000000ABCDEF"

    def test_write_to_log_redacts_key_in_fallback_error(self, tmp_path: Path):
        """write_to_log (called by both the Python path and equivalent to shell fallback)
        must redact API-key-shaped strings in the error text."""
        error_text = f"Setup failed: token={self.FAKE_KEY} is invalid"
        log_path = write_to_log(
            stage_label="[2/7]",
            error_text=error_text,
            exc=None,
            root=tmp_path,
        )
        content = log_path.read_text(encoding="utf-8")
        assert self.FAKE_KEY not in content
        assert "[REDACTED]" in content

    def test_build_console_summary_redacts_key_in_fallback_summary(self, tmp_path: Path):
        """build_console_summary must redact key-like strings in the one-line summary
        (mirrors what the shell fallback prints to console)."""
        log_path = tmp_path / "setup.log"
        log_path.write_text("log", encoding="utf-8")
        summary_with_key = f"Auth error: {self.FAKE_KEY}"
        summary = build_console_summary(2, 7, summary_with_key, log_path)
        assert self.FAKE_KEY not in summary
        assert "[REDACTED]" in summary

    def test_redact_handles_AIza_prefix(self):
        """_redact must strip AIza-prefixed keys (Google / Gemini style)."""
        key = "AIzaSyFAKEKEY0000000000000000000000000"
        text = f"failed with key={key}"
        result = _redact(text)
        assert key not in result
        assert "[REDACTED]" in result
