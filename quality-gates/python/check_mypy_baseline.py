#!/usr/bin/env python
"""G2 — mypy (strict) typecheck, baselined: FAIL only on NEW errors relative to a
version-controlled baseline (quality-gates/python/mypy-baseline.json). Same pattern as G1/G4.

This tree carries 123 pre-existing mypy strict errors across 16 files (measured 2026-08-27,
concentrated in core/integration/workers.py, core/generation/service.py,
core/generation/adapters/comfyui.py, core/main.py) — not fixed by this gate, recorded in the
baseline for a human to burn down.

Identity key = "relative/file.py|CODE|message text", same line-drift-tolerant shape as G1.

--- Subprocess-crash fail-safe (added 2026-08-27, fresh-reviewer finding) ---

mypy's own exit codes: 0 = ran clean (0 errors), 1 = ran and found errors, 2 = a fatal error
(bad `--config-file`, internal crash, etc — mypy never actually typechecked anything). Only
0/1 mean "mypy actually analyzed the code". This script's per-line JSON parsing already
tolerates mypy's own trailing plain-text summary line on a NORMAL run (harmless, expected) —
but with no returncode check at all, a genuine crash (exit 2, empty/garbage stdout) fell
through that same per-line skip and silently produced 0 findings + PASS. Reproduced 2026-08-27
by pointing mypy at a nonexistent `--config-file`. `lib/tool_run.run_and_check` now raises
before any of that stdout is ever parsed, whenever the returncode isn't in {0, 1}.
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
BASELINE_PATH = Path(__file__).resolve().parent / "mypy-baseline.json"


def _run_mypy() -> list[dict]:
    proc = run_and_check(
        [sys.executable, "-m", "mypy", "core", "--output=json"],
        cwd=ROOT,
        ok_returncodes=(0, 1),  # 0 = clean, 1 = errors found — anything else is a crash
    )
    items = []
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            items.append(json.loads(line))
        except json.JSONDecodeError:
            continue  # a non-JSON summary line on an otherwise-successful run — harmless
    return [item for item in items if item.get("severity") == "error"]


def _identity(item: dict) -> str:
    rel = Path(item["file"]).as_posix()
    return f"{rel}|{item['code']}|{item['message']}"


def main() -> int:
    update_mode = "--update-baseline" in sys.argv[1:]
    try:
        items = _run_mypy()
    except ToolCrashedError as e:
        print(f"[G2] FAIL - mypy crashed instead of running cleanly:\n{e}", file=sys.stderr)
        return 1
    current = sorted({_identity(i) for i in items})

    if update_mode:
        baseline_lib.write(BASELINE_PATH, current)
        print(f"[G2] baseline updated - {len(current)} error(s) recorded at {BASELINE_PATH.name}.")
        return 0

    baseline = baseline_lib.load(BASELINE_PATH)
    new, resolved = baseline_lib.diff(current, baseline)

    if resolved:
        print(
            f"[G2] note: {len(resolved)} baseline error(s) no longer exist - "
            "consider re-running with --update-baseline to shrink the baseline:"
        )
        for v in resolved:
            print(f"  - {v}")

    if new:
        print(f"[G2] FAIL - {len(new)} NEW mypy error(s) not present in the baseline:", file=sys.stderr)
        for v in new:
            print(f"  - {v}", file=sys.stderr)
        print(
            f"\nBaseline: {BASELINE_PATH.name} ({len(baseline)} pre-existing error(s), unaffected).",
            file=sys.stderr,
        )
        return 1

    print(f"[G2] PASS - {len(current)} total error(s), 0 new vs baseline ({len(baseline)} pre-existing).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
