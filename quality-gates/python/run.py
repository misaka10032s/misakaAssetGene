#!/usr/bin/env python
"""Aggregate quality-gate runner (Python family recipe).

Usage (from repo root):
    py -3.11 quality-gates/python/run.py <g1|g2|g3|g4|g5|l0|l1> [--update-baseline]
    (or, using this repo's own uv-managed venv: .venv/Scripts/python quality-gates/python/run.py ...)

  l0 = G1 (ruff, baselined) + G2 (mypy strict, baselined) + G3 (pytest + assertion-presence) +
       G4 (import-linter acyclic_siblings, baselined) - seconds-level, mirrors the JS-family
       recipe's l0/hook tier. G1/G2/G4 all fail only on NEW findings vs a version-controlled
       baseline file (quality-gates/python/{ruff,mypy,import-cycle}-baseline.json) - see
       lib/baseline.py and each gate's own docstring. `--update-baseline` re-snapshots the
       CURRENT findings as the new baseline (deliberate, reviewed cleanup or accepted new
       debt only - never a bypass).
  l1 = l0 + G5 (diff coverage, >=60% of changed lines)
       - G6 (diff mutation / mutmut) is REMOVED for this family, cluster-wide (see
         .claude/CLAUDE.md `## Code quality gates`): mutmut 3.x refuses to run on native
         Windows at all ("To run mutmut on Windows, please use the WSL."), exit code 1,
         unconditionally, before mutating anything. Not attempted here.

Every gate here is a thin wrapper around a real external command run against ROOT (repo
root, computed from this file's own location) - this script's only job is consistent
naming/sequencing (mirrors the JS-family recipe's `gate:g1..gate:l1` npm scripts).
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib.git_diff import ensure_utf8_stdio

ensure_utf8_stdio()

ROOT = Path(__file__).resolve().parent.parent.parent  # repo root
GATES_DIR = Path(__file__).resolve().parent  # quality-gates/python/


def _run(cmd: list[str]) -> int:
    print(f"$ {' '.join(cmd)}")
    return subprocess.run(cmd, cwd=ROOT).returncode


def g1(update_baseline: bool = False) -> int:
    cmd = [sys.executable, str(GATES_DIR / "check_ruff_baseline.py")]
    if update_baseline:
        cmd.append("--update-baseline")
    return _run(cmd)


def g2(update_baseline: bool = False) -> int:
    cmd = [sys.executable, str(GATES_DIR / "check_mypy_baseline.py")]
    if update_baseline:
        cmd.append("--update-baseline")
    return _run(cmd)


def g3() -> int:
    rc = _run([sys.executable, "-m", "pytest", "-q"])
    if rc != 0:
        return rc
    return _run([sys.executable, str(GATES_DIR / "check_test_assertions.py")])


def g4(update_baseline: bool = False) -> int:
    cmd = [sys.executable, str(GATES_DIR / "check_import_cycles.py")]
    if update_baseline:
        cmd.append("--update-baseline")
    return _run(cmd)


def g5() -> int:
    rc = _run([sys.executable, "-m", "pytest", "-q", "--cov=core", "--cov-report=xml"])
    if rc != 0:
        return rc
    return _run([sys.executable, str(GATES_DIR / "diff_coverage.py")])


def l0(update_baseline: bool = False) -> int:
    gates = (
        lambda: g1(update_baseline),
        lambda: g2(update_baseline),
        g3,
        lambda: g4(update_baseline),
    )
    for gate in gates:
        rc = gate()
        if rc != 0:
            return rc
    return 0


def l1(update_baseline: bool = False) -> int:
    rc = l0(update_baseline)
    if rc != 0:
        return rc
    return g5()


GATES = {"g1": g1, "g2": g2, "g3": g3, "g4": g4, "g5": g5, "l0": l0, "l1": l1}


def main() -> int:
    if len(sys.argv) < 2 or sys.argv[1] not in GATES:
        print(f"usage: run.py <{'|'.join(GATES)}> [--update-baseline]", file=sys.stderr)
        return 2
    name = sys.argv[1]
    update_baseline = "--update-baseline" in sys.argv[2:]
    if name in ("g1", "g2", "g4", "l0", "l1"):
        return GATES[name](update_baseline)
    return GATES[name]()


if __name__ == "__main__":
    sys.exit(main())
