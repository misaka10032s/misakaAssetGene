"""Shared helper for gates that shell out to a CLI tool and parse its stdout as JSON/lines.

Every such gate MUST check the tool's own subprocess return code before trusting its stdout —
an empty or unparsable stdout from a CRASHED tool (bad config, missing dependency, wrong
interpreter, internal panic) must never be silently read as "0 findings" and passed.

This was a real, reproduced defect (2026-08-27, fresh-reviewer finding): pointing ruff at a
nonexistent `--config` file made it exit 2 with EMPTY stdout; `check_ruff_baseline.py`
originally did `json.loads(proc.stdout or "[]")` with no returncode check at all, so the empty
stdout silently became `[]` (0 violations) and the gate printed PASS — while the 180
already-baselined violations still sat untouched on disk and were never actually re-checked.
Same reproduction for mypy (`--config-file` pointed at a nonexistent path, exit 2, empty
stdout). `check_import_cycles.py` does NOT share this hole: it calls grimp's Python API
in-process rather than shelling out and parsing stdout, so a crash there is an uncaught
Python exception (loud, non-zero exit) by construction — verified 2026-08-27 by pointing it
at a nonexistent root package.
"""
from __future__ import annotations

import subprocess


class ToolCrashedError(RuntimeError):
    """Raised when a linter/typechecker subprocess exits with a code that does not mean
    'ran successfully and reported N findings' — i.e. it crashed rather than found nothing."""


def run_and_check(
    cmd: list[str], cwd, ok_returncodes: tuple[int, ...]
) -> subprocess.CompletedProcess:
    """Run `cmd`; raise ToolCrashedError if the return code is not one of the tool's own
    documented 'ran successfully' codes (e.g. ruff/mypy: 0 = clean, 1 = findings reported —
    anything else, e.g. 2, is a crash/bad-config/internal-error, never a legitimate finding
    count, and must never be parsed as if it were)."""
    proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if proc.returncode not in ok_returncodes:
        raise ToolCrashedError(
            f"`{' '.join(cmd)}` exited {proc.returncode} (expected one of {ok_returncodes}) — "
            "treating this as a CRASH, not '0 findings'.\n"
            f"--- stdout ---\n{proc.stdout}\n--- stderr ---\n{proc.stderr}"
        )
    return proc
