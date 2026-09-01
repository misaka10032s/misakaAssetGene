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

--- Guard-ordering fail-open, closed 2026-08-27 (sibling-repo reviewer finding, reproduced here) ---

`main()`'s `new`/`resolved` diff was already both computed and both printed before the single
`return 1` at the bottom (confirmed by inspection AND by reproduction: fixing one real baselined
violation while simultaneously planting one genuinely new one made the non-update run FAIL naming
BOTH — this repo was never vulnerable to the "resolved short-circuits before new is ever
evaluated" shape reported in the sibling repo). The REAL hole, reproduced here 2026-08-27, is one
level down: `--update-baseline` re-snapshotted `current` (`baseline_lib.write`) UNCONDITIONALLY,
with no diff printed at all — a developer who saw both blocks in a plain run and, following the
`resolved` block's own advice, ran `--update-baseline` while an unrelated NEW violation was also
present, had that violation silently baked into the baseline as permanent accepted debt with zero
visibility (measured: baseline count stayed unchanged at 180 — one entry removed, a different,
unrelated one added — nothing in the command's own output named what just got accepted).

Fixed identically to check_mypy_baseline.py's G2, by extracting the whole reporting/decision
shape into ONE shared function, `baseline_lib.report_and_decide()`, used identically by this
gate, G2, and G4 (`check_import_cycles.py`) — see that function's docstring in `lib/baseline.py`,
including why an earlier "write anyway, just print a warning" version of this fix was rejected.
`main()` here is now a thin wrapper: run ruff, compute `current`/`new`/`resolved`, hand them to
the shared function. `--update-baseline` REFUSES to write (exit 1, baseline file byte-for-byte
unchanged) whenever both new and resolved violations are present in the same run — new-only and
resolved-only both still proceed normally.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib import baseline as baseline_lib
from lib.baseline import BaselineCorruptError
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
    try:
        baseline = baseline_lib.load(BASELINE_PATH)
    except BaselineCorruptError as e:
        print(f"[G1] FAIL - baseline file problem, refusing to trust this run:\n{e}", file=sys.stderr)
        return 1
    new, resolved = baseline_lib.diff(current, baseline)
    return baseline_lib.report_and_decide(
        gate="G1",
        noun="ruff violation(s)",
        baseline_path=BASELINE_PATH,
        baseline=baseline,
        current=current,
        new=new,
        resolved=resolved,
        update_mode=update_mode,
    )


if __name__ == "__main__":
    sys.exit(main())
