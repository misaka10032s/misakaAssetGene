"""Generic version-controlled baseline-diff helper, shared by every gate that needs
"fail only on NEW findings relative to what already existed on this tree" (G1 ruff, G2 mypy,
G4 import-cycles — all three standardized onto this module 2026-08-27; G4's
check_import_cycles.py used to keep its own inline copy of the same diff idea).

Ported from D:/backup/CSIA/.WEB/learningMachine/api/quality-gates/lib/baseline.py (verified
recipe) — kept identical in spirit.

A baseline is a JSON array of violation *identity strings* — a stable key built by the
caller from fields that survive line drift (e.g. "relative/file.py|CODE|message text"),
deliberately EXCLUDING the line number so a violation that merely moved a few lines because
of an unrelated edit above it doesn't register as "new". Two violations with the same
identity are indistinguishable and collapse to one baseline entry — acceptable for this
recipe's scale (a few hundred pre-existing findings, not thousands).

--- Guard-ordering fail-open, closed 2026-08-27 (sibling-repo reviewer finding, reproduced here
in this repo's G1/G2; see check_mypy_baseline.py's module docstring for the full reproduction/
proof transcript) ---

`report_and_decide()` is the ONE shared decision used identically by every caller (G1, G2, G4)
so the gates cannot drift apart on this again. It always computes and reports BOTH `new` and
`resolved` before deciding anything — `new` first, since it is the actionable, blocking half —
and FAILs whenever either is non-empty. When both are present, the FAIL message explicitly warns
that `--update-baseline` is not a clean remedy while new findings are unaddressed: it snapshots
the CURRENT tree wholesale, so running it now would also silently bake the new finding(s) in as
accepted debt, not just shrink the baseline for the resolved one(s).

`--update-baseline` REFUSES to write when both `new` and `resolved` are non-empty in the same
run (exit 1, prints every finding in both sets by name, the baseline file is left byte-for-byte
UNCHANGED) — a first version of this fix instead let the write proceed while merely printing a
warning, but that "loud but not blocked" shape does not actually stop a scripted or
muscle-memory `--update-baseline && git commit` chain from absorbing the new finding anyway; it
only helps a human who reads the output before running the next command, which is exactly the
"if you are not reading closely enough" failure class this fix exists to close. `--update-baseline`
still proceeds normally, printing exactly what it accepts/removes, for the two SAFE cases:
new-only (this repo's own quality-gates docs, `.claude/CLAUDE.md` `## Code quality gates`,
document `--update-baseline` as a legitimate way to "knowingly accept a new pre-existing item")
and resolved-only (a pure baseline shrink). Only the AMBIGUOUS mixed case — shrink for resolved
findings while simultaneously growing for new ones, in one snapshot — is refused outright, forcing
the developer to deal with the new finding(s) first (fix them, or re-run once no resolved finding
sits alongside them) before the baseline can be updated at all.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path


class BaselineCorruptError(RuntimeError):
    """Raised when a baseline JSON file exists but does not parse as a JSON array of finding
    identity strings. A MISSING file is treated as an empty baseline (`load()` returns `[]`) —
    that is a normal "first run, no baseline yet" state. A file that exists but fails to parse
    is different and must never be silently coerced to an empty baseline too: that would erase
    every pre-existing finding and let a masked corruption pass every gate as if the tree were
    suddenly clean. Cheap fix (2026-08-27, sibling-repo finding): callers used to let a bare
    `json.JSONDecodeError` propagate as an uncaught traceback — it already failed loud (non-zero
    exit) so this was never a fail-open, but the raw traceback is worse diagnostics than a named
    gate FAIL message, so every gate's `main()` now catches this and prints one."""


def load(path: Path) -> list[str]:
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8")
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise BaselineCorruptError(
            f"{path} exists but is not valid JSON, so it cannot be trusted as a baseline. "
            f"Parse error: {e}"
        ) from e
    if not isinstance(data, list):
        raise BaselineCorruptError(
            f"{path} does not contain a JSON array of finding-identity strings (got "
            f"{type(data).__name__} instead) — refusing to trust it as a baseline."
        )
    return data


def write(path: Path, violations: list[str]) -> None:
    path.write_text(json.dumps(sorted(set(violations)), indent=2) + "\n", encoding="utf-8")


def diff(current: list[str], baseline: list[str]) -> tuple[list[str], list[str]]:
    """Returns (new_violations, resolved_violations), both sorted.

    new = present now, absent from the baseline (blocks the gate).
    resolved = present in the baseline, absent now. Every caller now ALSO blocks on a non-empty
    `resolved` via `report_and_decide()` below — a vanished finding is either a genuine
    improvement (fix it forward via --update-baseline) or a sign detection silently stopped
    covering something it used to, and no gate here passes silently on that ambiguity.
    """
    baseline_set = set(baseline)
    current_set = set(current)
    new = sorted(v for v in current if v not in baseline_set)
    resolved = sorted(v for v in baseline if v not in current_set)
    return new, resolved


def report_and_decide(
    *,
    gate: str,
    noun: str,
    baseline_path: Path,
    baseline: list[str],
    current: list[str],
    new: list[str],
    resolved: list[str],
    update_mode: bool,
) -> int:
    """Shared "report both new and resolved before deciding anything, FAIL if either is
    non-empty, never silently absorb a new finding via --update-baseline" decision used
    identically by G1 (ruff), G2 (mypy) and G4 (import cycles). See this module's docstring for
    the defect this closes.

    `gate` — short tag for log lines, e.g. "G1". `noun` — plural finding-noun for messages, e.g.
    "ruff violation(s)", "mypy error(s)", "cycle-breaking edge(s)".

    Returns the process exit code the caller's own `main()` should return verbatim.
    """
    if update_mode:
        if new and resolved:
            # REFUSE — do not touch the baseline file at all. See this module's docstring for
            # why "write anyway, just print a warning" is not good enough here.
            print(
                f"[{gate}] FAIL - --update-baseline refused: {len(new)} new {noun} AND "
                f"{len(resolved)} resolved {noun} are both present in this run. Baseline left "
                "UNCHANGED.",
                file=sys.stderr,
            )
            print(f"  NEW {noun} (not in the baseline — deal with these first):", file=sys.stderr)
            for v in new:
                print(f"    - {v}", file=sys.stderr)
            print(f"  RESOLVED {noun} (would have been removed):", file=sys.stderr)
            for v in resolved:
                print(f"    - {v}", file=sys.stderr)
            print(
                "\nRunning --update-baseline now would ambiguously mix shrinking the baseline "
                "for the resolved finding(s) with growing it for the new one(s) in a single "
                "snapshot. Fix or deliberately accept the new finding(s) first — once no "
                "resolved finding sits alongside them, --update-baseline will proceed normally.",
                file=sys.stderr,
            )
            return 1
        if new:
            print(
                f"[{gate}] --update-baseline will accept {len(new)} new {noun} as baselined "
                "debt:"
            )
            for v in new:
                print(f"  - {v}")
        if resolved:
            print(f"[{gate}] --update-baseline will remove {len(resolved)} resolved {noun}:")
            for v in resolved:
                print(f"  - {v}")
        write(baseline_path, current)
        print(f"[{gate}] baseline updated - {len(current)} {noun} recorded at {baseline_path.name}.")
        return 0

    if new:
        print(f"[{gate}] FAIL - {len(new)} NEW {noun} not present in the baseline:", file=sys.stderr)
        for v in new:
            print(f"  - {v}", file=sys.stderr)

    if resolved:
        print(
            f"[{gate}] FAIL - {len(resolved)} previously-baselined {noun} no longer exist:",
            file=sys.stderr,
        )
        for v in resolved:
            print(f"  - {v}", file=sys.stderr)
        if new:
            print(
                f"\nNEW {noun} were ALSO reported above — deal with those first. "
                "--update-baseline REFUSES to run while both new and resolved findings are "
                "present in the same run (see below) — it is not a remedy here, not even a "
                "no-op one.",
                file=sys.stderr,
            )
        else:
            print(
                f"\nA vanished baseline finding means either a genuine improvement, or that "
                "this gate silently stopped covering something it used to (a masked crash, a "
                "config that stopped applying, a narrowed detection scope, etc). This gate does "
                "not pass silently on that ambiguity. If the improvement is real, re-run with "
                "--update-baseline to shrink the baseline; otherwise investigate why these "
                f"{noun} vanished before trusting the tree.",
                file=sys.stderr,
            )

    if new or resolved:
        print(f"\nBaseline: {baseline_path.name} ({len(baseline)} pre-existing {noun}).", file=sys.stderr)
        return 1

    print(f"[{gate}] PASS - {len(current)} total {noun}, 0 new vs baseline ({len(baseline)} pre-existing).")
    return 0
