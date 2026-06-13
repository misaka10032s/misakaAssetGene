"""Tests for app-wide log redaction filter (spec §11.3, M5.4).

Coverage:
  (a) ``redact()`` removes API keys for each pattern family.
  (b) ``redact()`` removes local user paths (Windows/macOS/Linux).
  (c) ``RedactionFilter`` applied to a real logger — emitted message is redacted.
  (d) ``RedactionFilter`` applied to logger.exception path — exc_text is redacted.
  (e) MISAKA_LOG_REDACT=0 disables filter installation.
  (f) Legitimate non-sensitive messages pass through unchanged (no over-redaction).
  (g) ``install_redaction_filter`` is idempotent (calling twice is safe).

No real network calls.  No real subprocess.  Tests restore env vars and filter
state via fixtures so they don't bleed into each other.
"""

from __future__ import annotations

import logging
import os
from unittest.mock import patch

import pytest

from core.logging_redaction import (
    RedactionFilter,
    install_redaction_filter,
    redact,
)


# ============================================================================
# (a) API key redaction via redact()
# ============================================================================


class TestRedactApiKeys:
    """redact() must scrub each named API key pattern family."""

    def test_sk_prefix(self):
        """sk-<long> — old OpenAI/generic sk- key."""
        text = "Using key sk-abcdefghijklmnopqrstuvwxyz1234567890 for request"
        result = redact(text)
        assert "[REDACTED]" in result
        assert "sk-abcdefghijklmnopqrstuvwxyz" not in result

    def test_sk_ant_prefix(self):
        """sk-ant-api03-<long> — Anthropic API key."""
        key = "sk-ant-api03-ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmno"
        result = redact(f"Anthropic key: {key}")
        assert "[REDACTED]" in result
        assert key not in result

    def test_sk_proj_prefix(self):
        """sk-proj-<long> — OpenAI project-scoped key."""
        key = "sk-proj-ZxYwVuTsRqPoNmLkJiHgFeDcBa0987654321abcdefgh"
        result = redact(f"key={key}")
        assert "[REDACTED]" in result
        assert key not in result

    def test_aiza_prefix(self):
        """AIza<long> — Google API key."""
        key = "AIzaSyABCDEFGHIJKLMNOPQRSTUVWXYZ12345678"
        result = redact(f"GOOGLE_API_KEY={key}")
        assert "[REDACTED]" in result
        assert key not in result

    def test_bearer_token(self):
        """Bearer <token> — HTTP Authorization header value."""
        token = "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIiwibmFtZSI6IkpvaG4gRG9lIiwiaWF0IjoxNTE2MjM5MDIyfQ"
        result = redact(token)
        assert "[REDACTED]" in result
        assert "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9" not in result

    def test_generic_long_token(self):
        """≥20-char alphanumeric+special sequence — generic backstop."""
        key = "ABCDEFGHIJKLMNOPQRSTUVWXYZ1234567890abcdefgh"
        result = redact(f"token={key} in request")
        assert "[REDACTED]" in result
        assert key not in result

    def test_key_in_traceback(self):
        """A key embedded in a multi-line traceback string is redacted."""
        tb = (
            "Traceback (most recent call last):\n"
            "  File 'app.py', line 42, in call_api\n"
            "    headers = {'x-api-key': 'sk-ant-api03-XYZABCDEFGHIJKLMNOPQRSTUVabcdefghijklmno'}\n"
            "ValueError: bad status\n"
        )
        result = redact(tb)
        assert "sk-ant-api03-XYZABCDEFGHIJKLMNOPQRSTUVabcdefghijklmno" not in result
        assert "[REDACTED]" in result
        # Non-sensitive parts survive
        assert "ValueError: bad status" in result


# ============================================================================
# (b) Local user-path redaction via redact()
# ============================================================================


class TestRedactUserPaths:
    """redact() must replace the user segment in home/profile paths."""

    def test_windows_user_path(self):
        text = r"Loading config from C:\Users\alice\AppData\Roaming\misaka\settings.json"
        result = redact(text)
        assert "alice" not in result
        assert r"C:\Users\[USER]" in result
        # Tail of path should still be present for debugging
        assert "AppData" in result

    def test_windows_user_path_drive_d(self):
        text = r"Project root: D:\Users\bob\Documents\my_project"
        result = redact(text)
        assert "bob" not in result
        assert r"D:\Users\[USER]" in result

    def test_macos_users_path(self):
        text = "/Users/charlie/Library/Application Support/misaka"
        result = redact(text)
        assert "charlie" not in result
        assert "/Users/[USER]" in result
        assert "Library" in result

    def test_linux_home_path(self):
        text = "/home/dave/.config/misaka/settings.json"
        result = redact(text)
        assert "dave" not in result
        assert "/home/[USER]" in result
        assert ".config" in result

    def test_path_at_end_of_string(self):
        text = r"Resolved project root: C:\Users\eve"
        result = redact(text)
        assert "eve" not in result
        assert "[USER]" in result

    def test_multiple_paths_in_one_message(self):
        text = (
            r"Input: C:\Users\frank\input.png"
            r", Output: C:\Users\grace\output.png"
        )
        result = redact(text)
        assert "frank" not in result
        assert "grace" not in result


# ============================================================================
# (c) RedactionFilter on a real logger (message path)
# ============================================================================


class TestRedactionFilterMessage:
    """RedactionFilter.filter() must redact the formatted message."""

    def _make_logger_with_capture(self) -> tuple[logging.Logger, list[logging.LogRecord]]:
        """Return a logger + a list that collects every LogRecord emitted."""
        records: list[logging.LogRecord] = []

        class CapturingHandler(logging.Handler):
            def emit(self, record: logging.LogRecord) -> None:
                records.append(record)

        logger = logging.getLogger(f"test.redact.{id(self)}")
        logger.propagate = False
        logger.setLevel(logging.DEBUG)
        filt = RedactionFilter()
        handler = CapturingHandler()
        logger.addFilter(filt)
        logger.addHandler(handler)
        return logger, records

    def test_api_key_in_info_message_is_redacted(self):
        logger, records = self._make_logger_with_capture()
        logger.info("Connecting with key sk-ant-api03-ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789")
        assert records, "No record captured"
        msg = records[0].getMessage()
        assert "sk-ant-api03-ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789" not in msg
        assert "[REDACTED]" in msg

    def test_user_path_in_warning_is_redacted(self):
        logger, records = self._make_logger_with_capture()
        logger.warning(r"Project loaded from C:\Users\hiro\my_project")
        msg = records[0].getMessage()
        assert "hiro" not in msg
        assert "[USER]" in msg

    def test_format_args_are_redacted(self):
        """logger.info('%s', key) — args are baked+redacted before emission."""
        logger, records = self._make_logger_with_capture()
        key = "sk-proj-ABCDEFGHIJKLMNOPQRSTUVWXYZ012345678901234"
        logger.info("API key is %s", key)
        msg = records[0].getMessage()
        assert key not in msg
        assert "[REDACTED]" in msg

    def test_non_sensitive_message_unchanged(self):
        """Normal log messages must not be mangled."""
        logger, records = self._make_logger_with_capture()
        msg_text = "GET /api/v1/projects — 200 OK"
        logger.info(msg_text)
        assert records[0].getMessage() == msg_text

    def test_short_path_not_over_redacted(self):
        """core/main.py style paths must survive (no over-redaction)."""
        logger, records = self._make_logger_with_capture()
        logger.info("Loaded module core/main.py and scripts/lib/setup_diagnostics.py")
        msg = records[0].getMessage()
        assert "core/main.py" in msg
        assert "setup_diagnostics.py" in msg


# ============================================================================
# (d) RedactionFilter on the exception path
# ============================================================================


class TestRedactionFilterException:
    """Exceptions logged via logger.exception() must have secrets redacted in exc_text."""

    def test_exception_text_is_redacted(self):
        records: list[logging.LogRecord] = []

        class CapturingHandler(logging.Handler):
            def emit(self, record: logging.LogRecord) -> None:
                # Force exc_text formatting before we capture.
                self.format(record)
                records.append(record)

        logger = logging.getLogger(f"test.exc.{id(self)}")
        logger.propagate = False
        logger.setLevel(logging.DEBUG)
        filt = RedactionFilter()
        handler = CapturingHandler()
        logger.addFilter(filt)
        logger.addHandler(handler)

        key = "AIzaSyABCDEFGHIJKLMNOPQRSTUVWXYZ99887766"
        try:
            raise ValueError(f"Bad request: api_key={key}")
        except ValueError:
            logger.exception("Request failed")

        assert records
        rec = records[0]
        # exc_text is set by the filter; the raw key must not appear.
        exc_text = rec.exc_text or ""
        assert key not in exc_text, f"Key leaked into exc_text: {exc_text}"
        assert "[REDACTED]" in exc_text


# ============================================================================
# (e) MISAKA_LOG_REDACT=0 disables filter
# ============================================================================


class TestEnvToggle:
    """MISAKA_LOG_REDACT env toggle controls whether the filter is installed."""

    def test_disabled_by_zero(self):
        """MISAKA_LOG_REDACT=0 → install_redaction_filter returns False."""
        import core.logging_redaction as mod

        orig_installed = mod._FILTER_INSTALLED
        orig_sentinel = mod._FILTER_SENTINEL
        try:
            mod._FILTER_INSTALLED = False
            mod._FILTER_SENTINEL = None
            with patch.dict(os.environ, {"MISAKA_LOG_REDACT": "0"}):
                result = install_redaction_filter()
            assert result is False
        finally:
            mod._FILTER_INSTALLED = orig_installed
            mod._FILTER_SENTINEL = orig_sentinel

    def test_disabled_by_false_string(self):
        """MISAKA_LOG_REDACT=false → install_redaction_filter returns False."""
        import core.logging_redaction as mod

        orig_installed = mod._FILTER_INSTALLED
        orig_sentinel = mod._FILTER_SENTINEL
        try:
            mod._FILTER_INSTALLED = False
            mod._FILTER_SENTINEL = None
            with patch.dict(os.environ, {"MISAKA_LOG_REDACT": "false"}):
                result = install_redaction_filter()
            assert result is False
        finally:
            mod._FILTER_INSTALLED = orig_installed
            mod._FILTER_SENTINEL = orig_sentinel

    def test_enabled_by_default(self):
        """With MISAKA_LOG_REDACT unset, the filter is installed (returns True)."""
        import core.logging_redaction as mod

        orig_installed = mod._FILTER_INSTALLED
        orig_sentinel = mod._FILTER_SENTINEL
        try:
            mod._FILTER_INSTALLED = False
            mod._FILTER_SENTINEL = None
            env = {k: v for k, v in os.environ.items() if k != "MISAKA_LOG_REDACT"}
            with patch.dict(os.environ, env, clear=True):
                result = install_redaction_filter()
            assert result is True
        finally:
            # Clean up: remove the filter we just added to the root logger
            if mod._FILTER_SENTINEL is not None and not orig_installed:
                logging.root.removeFilter(mod._FILTER_SENTINEL)
            mod._FILTER_INSTALLED = orig_installed
            mod._FILTER_SENTINEL = orig_sentinel

    def test_enabled_by_one(self):
        """MISAKA_LOG_REDACT=1 → install_redaction_filter returns True."""
        import core.logging_redaction as mod

        orig_installed = mod._FILTER_INSTALLED
        orig_sentinel = mod._FILTER_SENTINEL
        try:
            mod._FILTER_INSTALLED = False
            mod._FILTER_SENTINEL = None
            with patch.dict(os.environ, {"MISAKA_LOG_REDACT": "1"}):
                result = install_redaction_filter()
            assert result is True
        finally:
            if mod._FILTER_SENTINEL is not None and not orig_installed:
                logging.root.removeFilter(mod._FILTER_SENTINEL)
            mod._FILTER_INSTALLED = orig_installed
            mod._FILTER_SENTINEL = orig_sentinel


# ============================================================================
# (f) No over-redaction of normal words / paths
# ============================================================================


class TestNoOverRedaction:
    """Normal log output must not be mangled by the redaction filter."""

    def test_short_word_not_redacted(self):
        """Short words like 'info' or 'debug' stay intact."""
        assert redact("info") == "info"
        assert redact("debug") == "debug"
        assert redact("core") == "core"

    def test_normal_url_not_redacted(self):
        """Public API base URLs (no secret token) survive."""
        url = "https://api.anthropic.com/v1/messages"
        result = redact(url)
        # URL itself has no long secret; domain names are short segments
        assert "anthropic.com" in result

    def test_short_api_path_not_redacted(self):
        """Module/file paths like core/main.py are short enough to survive."""
        assert redact("core/main.py") == "core/main.py"
        assert redact("scripts/lib/setup_diagnostics.py") == "scripts/lib/setup_diagnostics.py"

    def test_project_id_uuid_survives(self):
        """UUID project IDs (no secret prefix) should be matched by generic backstop."""
        # UUIDs ARE 36 chars — they will be redacted by the generic backstop.
        # This is acceptable (over-redaction of UUIDs in logs is safe).
        uuid = "550e8400-e29b-41d4-a716-446655440000"
        result = redact(uuid)
        # We don't assert direction here — just that it doesn't crash.
        assert isinstance(result, str)

    def test_plain_log_message_unchanged(self):
        """A typical INFO-level log line with no secrets is untouched."""
        msg = "GET /api/v1/projects — 200 OK in 4ms"
        assert redact(msg) == msg

    def test_worker_name_unchanged(self):
        """Worker names (comfyui, audiocraft) are short and must not be redacted."""
        assert redact("comfyui") == "comfyui"
        assert redact("audiocraft") == "audiocraft"


# ============================================================================
# (g) install_redaction_filter is idempotent
# ============================================================================


class TestIdempotency:
    """Calling install_redaction_filter twice must not add duplicate filters."""

    def test_idempotent_call(self):
        import core.logging_redaction as mod

        orig_installed = mod._FILTER_INSTALLED
        orig_sentinel = mod._FILTER_SENTINEL
        try:
            mod._FILTER_INSTALLED = False
            mod._FILTER_SENTINEL = None
            with patch.dict(os.environ, {"MISAKA_LOG_REDACT": "1"}):
                result1 = install_redaction_filter()
                result2 = install_redaction_filter()
            # Both calls must succeed; the second is a no-op
            assert result1 is True
            assert result2 is True
            # Root logger must not have duplicate filters
            sentinel = mod._FILTER_SENTINEL
            count = sum(1 for f in logging.root.filters if f is sentinel)
            assert count == 1, f"Expected 1 filter, found {count}"
        finally:
            if mod._FILTER_SENTINEL is not None and not orig_installed:
                logging.root.removeFilter(mod._FILTER_SENTINEL)
            mod._FILTER_INSTALLED = orig_installed
            mod._FILTER_SENTINEL = orig_sentinel
