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

Re-verified 2026-08-27 (cross-repo mypy fail-open investigation — see
D:/backup/CSIA/@PM/state/runs/CROSS-REPO-mypy-failopen.md): ruff, unlike mypy, does NOT have a
mypy-style silent-fallback-to-defaults hole on THIS repo. Both a syntactically broken
`[tool.ruff` (missing `]`) and a single unrecognized key under an otherwise-valid `[tool.ruff]`
table (`not_a_real_ruff_option`) made ruff exit 2 with EMPTY stdout — caught by the
`ok_returncodes=(0, 1)` guard above, not a fail-open. (A sibling repo, ns-media-hub, hit exactly
this exit-2/empty-stdout shape and it silently became "0 violations" there — the guard here is
what prevents the same outcome, so no Part-A-style pre-flight config validation was added for
ruff on this repo.) A moved-away `pyproject.toml` was NOT independently testable in this
repo's worktree layout — worktrees live nested inside the repo
(`.claude/worktree/<name>/`), so removing the LOCAL copy makes ruff's own upward directory
search fall through to the MAIN tree's `pyproject.toml` one level up, which is a worktree-
layout artifact, not evidence about ruff's real "config truly absent" behavior.

--- Vanished-baseline fail-safe (added 2026-08-27, cross-repo mypy fail-open investigation) ---

Applied here for consistency even though this repo's own G1 was not found vulnerable to the
mypy-style hole above: a baselined violation disappearing is either a genuine improvement or a
sign this gate silently stopped running as configured (this exact symptom is what a masked
ruff crash looks like in ns-media-hub's sibling case). `main()` below now FAILs on any
`resolved` violation instead of printing it as an informational note, naming exactly which
ones vanished and pointing at `--update-baseline` for a deliberate cleanup.
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
            f"[G1] FAIL - {len(resolved)} previously-baselined ruff violation(s) no longer exist:",
            file=sys.stderr,
        )
        for v in resolved:
            print(f"  - {v}", file=sys.stderr)
        print(
            "\nA vanished baseline violation means either a genuine improvement, or that this "
            "gate silently stopped running as configured (a crashed ruff subprocess masked as "
            "'0 violations', a config that stopped applying, etc). This gate does not pass "
            "silently on that ambiguity. If the improvement is real, re-run with "
            "--update-baseline to shrink the baseline; otherwise investigate why these "
            "violations vanished before trusting the tree.",
            file=sys.stderr,
        )

    if new:
        print(f"[G1] FAIL - {len(new)} NEW ruff violation(s) not present in the baseline:", file=sys.stderr)
        for v in new:
            print(f"  - {v}", file=sys.stderr)

    if new or resolved:
        print(
            f"\nBaseline: {BASELINE_PATH.name} ({len(baseline)} pre-existing violation(s)).",
            file=sys.stderr,
        )
        return 1

    print(f"[G1] PASS - {len(current)} total violation(s), 0 new vs baseline ({len(baseline)} pre-existing).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
