#!/usr/bin/env python
"""G4 - "no NEW import cycle relative to a version-controlled baseline."

Tool: import-linter's `acyclic_siblings` contract (`[tool.importlinter]` in pyproject.toml,
root_package="core", ancestors=["core"]). Deliberately NOT a hand-authored `layers` contract
(the pattern learningMachine's api/ used): core/ is ~13 peer subsystems (consultant, editor,
generation, integration, llm, memory, models, network, project, reporting, scheduler,
training, plus main.py/config.py) with no single strict dependency order across them - forcing
an artificial layer ordering would either be wrong on day one or need architecture archaeology
outside this task's scope. `acyclic_siblings` checks the same "no cycle between siblings"
invariant the JS-family recipe gets from madge's raw cycle detector, recursively at every
nesting depth, without assuming an ordering that does not exist.

This script calls grimp directly (`ImportGraph.nominate_cycle_breakers`) rather than
regex-parsing `lint-imports`' human-readable CLI text: `acyclic_siblings`'s own `check()`
method (importlinter/contracts/acyclic_siblings.py) calls exactly this same grimp method for
each ancestor, so this is the identical underlying algorithm the pyproject.toml contract
declares - just consumed as a stable (importer, imported) tuple API instead of parsing prose
that could reformat across import-linter versions.

This does NOT clean up pre-existing cycles - it only blocks NEW ones. Baseline: 2 pre-existing
edges (measured 2026-08-27): core.integration.workers -> core.generation.adapters.comfyui,
core.network.service -> core.models.schemas.

--- Fail-safe file-discovery check (added 2026-08-27, same root cause as G5's diff_coverage.py) ---

grimp's `build_graph` walks `core` the same `pkgutil`-style way coverage.py's `source=`
unexecuted-file discovery does, and has the IDENTICAL blind spot: a directory under `core/`
with no `__init__.py` (an implicit PEP 420 namespace package) is invisible to it entirely.
Confirmed empirically while fixing G5's coordinate vacuity: `core/reporting/` was exactly such
a directory, and `grimp.build_graph('core').modules` contained ZERO `core.reporting.*` entries
until `core/reporting/__init__.py` was added (now is). A cycle involving an invisible module
would have silently never been detected - the same fail-OPEN species as the G5 bug, just
manifesting as "never flagged" instead of "diff-cover says nothing to check". `_find_undiscovered_files`
below independently verifies every `.py` file that actually exists under `core/` on disk has a
corresponding entry in grimp's module list; any gap fails the gate loud rather than silently
leaving a blind spot in the cycle graph.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib.git_diff import ensure_utf8_stdio

ensure_utf8_stdio()

ROOT = Path(__file__).resolve().parent.parent.parent  # repo root
BASELINE_PATH = Path(__file__).resolve().parent / "import-cycle-baseline.json"
ROOT_PACKAGE = "core"


def _expected_module_name(py_file: Path) -> str:
    rel = py_file.relative_to(ROOT).with_suffix("")
    parts = list(rel.parts)
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _find_undiscovered_files(graph) -> list[str]:
    """Every core/**/*.py file on disk whose expected module name is NOT in grimp's graph -
    i.e. a file the cycle-detection algorithm never even looked at."""
    discovered = set(graph.modules)
    undiscovered = []
    for py_file in sorted((ROOT / ROOT_PACKAGE).rglob("*.py")):
        if "__pycache__" in py_file.parts:
            continue
        module = _expected_module_name(py_file)
        if module not in discovered:
            undiscovered.append(f"{py_file.relative_to(ROOT).as_posix()} (expected module '{module}')")
    return undiscovered


def _find_cycle_breakers() -> tuple[list[str], list[str]]:
    import grimp

    sys.path.insert(0, str(ROOT))
    graph = grimp.build_graph(ROOT_PACKAGE)
    breakers = graph.nominate_cycle_breakers(ROOT_PACKAGE)
    undiscovered = _find_undiscovered_files(graph)
    return sorted(f"{importer} -> {imported}" for importer, imported in breakers), undiscovered


def _load_baseline() -> list[str]:
    if not BASELINE_PATH.exists():
        return []
    return json.loads(BASELINE_PATH.read_text(encoding="utf-8"))


def main() -> int:
    update_mode = "--update-baseline" in sys.argv[1:]
    current, undiscovered = _find_cycle_breakers()

    # --- fail-safe check FIRST: a file grimp never discovered was never actually analyzed for
    # cycles, so it must never be silently treated as "no cycle found" ---
    if undiscovered:
        print(
            f"[G4] FAIL - {len(undiscovered)} file(s) under {ROOT_PACKAGE}/ were never "
            "discovered by grimp's import graph (likely an implicit namespace-package "
            "directory missing __init__.py) - cycle detection never ran on them:",
            file=sys.stderr,
        )
        for f in undiscovered:
            print(f"  - {f}", file=sys.stderr)
        print(
            "\n[G4] Add the missing __init__.py (or otherwise make the directory a regular "
            "package) so import-cycle detection actually covers this code, then re-run.",
            file=sys.stderr,
        )
        return 1

    if update_mode:
        BASELINE_PATH.write_text(json.dumps(current, indent=2) + "\n", encoding="utf-8")
        print(f"[G4] baseline updated - {len(current)} cycle-breaking edge(s) recorded at {BASELINE_PATH.name}.")
        return 0

    baseline = _load_baseline()
    new = [v for v in current if v not in baseline]
    resolved = [v for v in baseline if v not in current]

    if resolved:
        print(f"[G4] note: {len(resolved)} baseline edge(s) no longer exist - consider re-running with --update-baseline to shrink the baseline:")
        for v in resolved:
            print(f"  - {v}")

    if new:
        print(f"[G4] FAIL - {len(new)} NEW import cycle edge(s) not present in the baseline:", file=sys.stderr)
        for v in new:
            print(f"  - {v}", file=sys.stderr)
        print(f"\nBaseline: {BASELINE_PATH.name} ({len(baseline)} pre-existing edge(s), unaffected).", file=sys.stderr)
        return 1

    print(f"[G4] PASS - {len(current)} total cycle-breaking edge(s), 0 new vs baseline ({len(baseline)} pre-existing).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
