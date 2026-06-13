"""App-wide log redaction filter (spec §11.3, M5.4).

This module provides:
  - ``REDACT_RE`` — the single, precompiled regex covering all secret patterns.
  - ``redact(text)`` — pure function; replace secrets + local user paths.
  - ``RedactionFilter`` — ``logging.Filter`` subclass; installs on any logger.
  - ``install_redaction_filter()`` — installs the filter on the root logger so
    every logger in the process inherits it automatically.

Controlled by env var ``MISAKA_LOG_REDACT`` (default: ``"1"`` = ON).
Set it to ``"0"`` or ``"false"`` to disable for local deep-debugging.

Security design:
  - Runs BEFORE any handler emits, so secrets never reach file or stdout.
  - Applies to ``record.getMessage()`` (the formatted message), formatted
    exception text, and the stack-info string.
  - Over-redaction risk: the 20-char generic backstop can match long tokens in
    normal text.  The tests in ``tests/test_logging_redaction.py`` include a
    ``test_no_over_redaction`` suite to guard short / normal path strings.
  - Performance: all patterns are precompiled into a single ``re.compile``
    with alternation — one pass per record.
"""

from __future__ import annotations

import logging
import os
import re
import traceback
from typing import Optional


# ---------------------------------------------------------------------------
# Shared secret-pattern regex (single source of truth for the whole codebase)
# ---------------------------------------------------------------------------
# Design notes:
#   - Named key prefixes (sk-, sk-ant-, sk-proj-, AIza, Bearer) are matched
#     first; the generic ≥20-char backstop catches everything else.
#   - ``Bearer`` is followed by \s+ to consume whitespace before the token.
#   - The generic pattern requires [A-Za-z0-9] as first char so pure-ASCII
#     short words (e.g. "path") don't accidentally match.
#   - re.VERBOSE lets us comment each alternation branch.

REDACT_RE: re.Pattern[str] = re.compile(
    r"""
    (?:
        # --- Named API-key prefixes (most specific, matched first) ---
        # Anthropic:  sk-ant-api03-<body>
        # OpenAI project-scoped:  sk-proj-<body>
        # Generic sk-:  sk-<body>
        (?:sk-ant-api03-|sk-proj-|sk-)
        [A-Za-z0-9_\-]{10,}
    |
        # Google API key: AIza<body>  or  AIZA<body>
        (?:AIza|AIZA)
        [A-Za-z0-9_\-]{10,}
    |
        # Bearer token — HTTP Authorization: Bearer <token>
        # Token body may contain alphanumeric, +, /, =, _, -, . (base64url / JWT)
        # but we stop at whitespace so the whole header isn't swallowed.
        Bearer\s+
        [A-Za-z0-9+/=_\-\.]{10,}
    |
        # Generic long token: ≥20 chars of alphanumeric + base64-alphabet chars.
        # Excludes "." and "/" so that:
        #   - Normal file paths (e.g. scripts/lib/setup_diagnostics.py) are NOT
        #     swallowed — "/" would chain path segments into a long run.
        #   - URLs (e.g. https://api.anthropic.com/v1) are NOT swallowed — both
        #     "." (domain) and "/" (path) would chain them into a run.
        # This means pure-base64 tokens that rely on "/" chars will NOT be caught
        # by this backstop, but they ARE caught by the named-prefix patterns above
        # (sk-*, AIza*, Bearer).  The backstop is for tokens that don't have a
        # recognisable prefix — those almost always use alphanumeric+underscore
        # encoding (URL-safe base64 or hex), not standard base64 with "/".
        [A-Za-z0-9][A-Za-z0-9+_=\-]{19,}
    )
    """,
    re.VERBOSE,
)


# ---------------------------------------------------------------------------
# Local user-path regex
# ---------------------------------------------------------------------------
# Matches the user-name segment of a home/profile directory so that the
# OS account name is not leaked in logs.  We replace only the user name
# portion with ``[USER]``, keeping the rest of the path for debugging.
#
# Patterns covered:
#   Windows:  C:\Users\<name>\...  →  C:\Users\[USER]\...
#   macOS:    /Users/<name>/...    →  /Users/[USER]/...
#   Linux:    /home/<name>/...     →  /home/[USER]/...

_PATH_RE: re.Pattern[str] = re.compile(
    r"""
    (?:
        # Windows: drive:\Users\<name>
        [A-Za-z]:\\[Uu]sers\\
        (?P<win_user>[^\\/<>:"|?*\r\n]{1,64})
        (?=\\|$)
    |
        # macOS: /Users/<name>
        /[Uu]sers/
        (?P<mac_user>[^/\r\n]{1,64})
        (?=/|$)
    |
        # Linux: /home/<name>
        /home/
        (?P<linux_user>[^/\r\n]{1,64})
        (?=/|$)
    )
    """,
    re.VERBOSE,
)


def _redact_path(m: re.Match[str]) -> str:
    """Replace the user segment in a matched path with ``[USER]``."""
    full = m.group(0)
    if m.group("win_user"):
        # Replace only the user name part
        return full.replace(m.group("win_user"), "[USER]", 1)
    if m.group("mac_user"):
        return full.replace(m.group("mac_user"), "[USER]", 1)
    if m.group("linux_user"):
        return full.replace(m.group("linux_user"), "[USER]", 1)
    return full


# ---------------------------------------------------------------------------
# Public redact() function
# ---------------------------------------------------------------------------

def redact(text: str) -> str:
    """Redact API keys and local user paths from *text*.

    Args:
        text: Any string (log message, traceback, etc.).

    Returns:
        The string with secrets replaced by ``[REDACTED]`` and user names in
        home/profile paths replaced by ``[USER]``.
    """
    # Apply secret pattern first, then path pattern.
    text = REDACT_RE.sub("[REDACTED]", text)
    text = _PATH_RE.sub(_redact_path, text)
    return text


# ---------------------------------------------------------------------------
# logging.Filter — applied to every LogRecord before emission
# ---------------------------------------------------------------------------

class RedactionFilter(logging.Filter):
    """A ``logging.Filter`` that scrubs secrets from every log record.

    Applies ``redact()`` to:
      - The formatted message (``record.getMessage()`` result is baked back
        into ``record.msg`` with args cleared so the handler sees it once).
      - ``record.exc_text`` (already-formatted exception string, if present).
      - ``record.stack_info`` (stack-info string, if present).

    Because args are cleared after baking, the filter is idempotent: running
    it twice does not double-redact (the ``[REDACTED]`` placeholder is short
    enough not to match REDACT_RE again).
    """

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: A003
        # 1. Bake message + args into msg, then redact.
        try:
            msg = record.getMessage()
        except Exception:  # noqa: BLE001
            msg = str(record.msg)
        record.msg = redact(msg)
        record.args = None  # args already consumed; prevent double-formatting

        # 2. Redact formatted exception text (set by handlers after format()).
        if record.exc_info and record.exc_text is None:
            # Force format now so we can redact it before it's written.
            record.exc_text = redact(
                "".join(traceback.format_exception(*record.exc_info))
            )
            record.exc_info = None  # prevent double-formatting by handler
        elif record.exc_text:
            record.exc_text = redact(record.exc_text)

        # 3. Redact stack_info.
        if record.stack_info:
            record.stack_info = redact(record.stack_info)

        return True  # always emit (we never suppress records, only scrub them)


# ---------------------------------------------------------------------------
# App-wide installation helper
# ---------------------------------------------------------------------------

_FILTER_INSTALLED: bool = False
_FILTER_SENTINEL: Optional[RedactionFilter] = None


def install_redaction_filter() -> bool:
    """Install ``RedactionFilter`` on the root logger (idempotent).

    Controlled by ``MISAKA_LOG_REDACT`` env var:
      - ``"1"`` (default) or any truthy value → filter installed.
      - ``"0"`` or ``"false"`` → filter NOT installed; raw logs pass through.

    Returns:
        True if the filter was installed (or already installed), False if
        disabled by the env toggle.
    """
    global _FILTER_INSTALLED, _FILTER_SENTINEL

    raw = os.environ.get("MISAKA_LOG_REDACT", "1").strip().lower()
    enabled = raw not in ("0", "false", "no", "off")

    if not enabled:
        return False

    if _FILTER_INSTALLED:
        return True

    _FILTER_SENTINEL = RedactionFilter()
    logging.root.addFilter(_FILTER_SENTINEL)
    _FILTER_INSTALLED = True
    return True
