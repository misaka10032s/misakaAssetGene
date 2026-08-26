#!/usr/bin/env python
"""G1 — ruff lint, baselined: FAIL only on NEW violations relative to a version-controlled
baseline (quality-gates/python/ruff-baseline.json).

Why baselined rather than a bare `ruff check .`: this tree carries 269 pre-existing
violations (measured 2026-08-27, mostly F401 unused-import, UP017/UP035/UP037/UP042/UP045
modernize-typing-syntax, I001 import-sort, E402) at the time this gate was wired — a bare
pass/fail would make L0 permanently red on the untouched tree. This gate does NOT fix those
269 violations — they stay in the baseline for a human to burn down; `--update-baseline` is
for a deliberate, reviewed cleanup (or knowingly accepting a new one), never a blanket bypass.

Identity key = "relative/file.py|CODE|message text" — deliberately excludes the line number
so a violation that merely shifted a few lines from an unrelated edit above it doesn't
register as new.

--- Subprocess-crash fail-safe (added 2026-08-27, fresh-reviewer finding) ---

Ruff's own exit codes: 0 = ran clean (0 violations), 1 = ran and found violations, 2 = the
tool itself failed to run (bad `--config`, internal panic, etc). Only 0/1 mean "ruff actually
analyzed the code"; a `--output-format=json` run that exits 2 prints NOTHING to stdout, and
this script used to do `json.loads(proc.stdout or "[]")` unconditionally — turning that empty
stdout into "0 violations" and printing PASS even though ruff never actually ran. Reproduced
2026-08-27 by pointing ruff at a nonexistent `--config` file. `lib/tool_run.run_and_check`
below now raises instead of silently returning an empty list whenever the returncode isn't in
{0, 1}.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib import baseline as baseline_lib
from lib.git_diff import ensure_utf8_stdio
from lib.tool_run import ToolCrashedError, run_and_check

ensure_utf8_stdio()

ROOT = Path(__file__).resolve().parent.parent.parent  # repo root
BASELINE_PATH = Path(__file__).resolve().parent / "ruff-baseline.json"


def _run_ruff() -> list[dict]:
    proc = run_and_check(
        [sys.executable, "-m", "ruff", "check", ".", "--output-format=json"],
        cwd=ROOT,
        ok_returncodes=(0, 1),  # 0 = clean, 1 = violations found — anything else is a crash
    )
    # ruff writes the JSON report to stdout when it actually ran (returncode 0 or 1 above).
    return json.loads(proc.stdout or "[]")


def _identity(item: dict) -> str:
    rel = Path(item["filename"]).resolve().relative_to(ROOT).as_posix()
    return f"{rel}|{item['code']}|{item['message']}"


def main() -> int:
    update_mode = "--update-baseline" in sys.argv[1:]
    try:
        items = _run_ruff()
    except ToolCrashedError as e:
        print(f"[G1] FAIL - ruff crashed instead of running cleanly:\n{e}", file=sys.stderr)
        return 1
    current = sorted({_identity(i) for i in items})

    if update_mode:
        baseline_lib.write(BASELINE_PATH, current)
        print(f"[G1] baseline updated - {len(current)} violation(s) recorded at {BASELINE_PATH.name}.")
        return 0

    baseline = baseline_lib.load(BASELINE_PATH)
    new, resolved = baseline_lib.diff(current, baseline)

    if resolved:
        print(
            f"[G1] note: {len(resolved)} baseline violation(s) no longer exist - "
            "consider re-running with --update-baseline to shrink the baseline:"
        )
        for v in resolved:
            print(f"  - {v}")

    if new:
        print(f"[G1] FAIL - {len(new)} NEW ruff violation(s) not present in the baseline:", file=sys.stderr)
        for v in new:
            print(f"  - {v}", file=sys.stderr)
        print(
            f"\nBaseline: {BASELINE_PATH.name} ({len(baseline)} pre-existing violation(s), unaffected).",
            file=sys.stderr,
        )
        return 1

    print(f"[G1] PASS - {len(current)} total violation(s), 0 new vs baseline ({len(baseline)} pre-existing).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
