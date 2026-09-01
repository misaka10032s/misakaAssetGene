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

--- Recursion fix (2026-08-27, fresh-reviewer finding C) ---

This script now instantiates import-linter's own `AcyclicSiblingsContract`
(importlinter.contracts.acyclic_siblings) directly and calls its `check()` method, rather than
calling `graph.nominate_cycle_breakers('core')` once at the top level. The single top-level
call only checks for cycles among the DIRECT children of `core` (e.g. core.consultant vs
core.editor) - `graph.nominate_cycle_breakers(package)` is NOT itself recursive; that is why
`AcyclicSiblingsContract._nominate_cycle_breakers_recursively` exists: it calls
`nominate_cycle_breakers` once per level and then recurses into every child package
(`graph.find_children`), down to `depth` levels (default 10). A cycle nested entirely WITHIN
one subsystem (e.g. core/reporting/cycle_a.py <-> core/reporting/cycle_b.py, both children of
core.reporting rather than of core) is only visible at the `core.reporting` recursion level,
never at the top-level `core` call - confirmed missed by the old single-call version, and
confirmed caught once recursion runs (see baseline history below).

Using the real `AcyclicSiblingsContract` class (not a hand-rolled walk) means this script gets
import-linter's actual recursive algorithm verbatim - including its recursion depth, its
disjoint per-level child sets (which is what keeps the same edge from ever being reported
twice at two different recursion levels: each level only pairs up the direct children of ITS
OWN ancestor, and those children sets never overlap across levels), and its handling of
`skip_descendants`/`ignore_imports` if this repo ever adds them. `_load_contract_options()`
below reads the exact `[[tool.importlinter.contracts]]` block with `type = "acyclic_siblings"`
straight out of this repo's own `pyproject.toml` (via import-linter's own `read_user_options`),
so the script's contract config can never silently drift from what `pyproject.toml` declares -
no duplicated `ancestors = ["core"]` literal to keep in sync by hand.

This does NOT clean up pre-existing cycles - it only blocks NEW ones. Baseline (re-measured
2026-08-27, POST-recursion-fix, `.grimp_cache/` cleared first): still exactly the same 2
cross-subsystem edges as the pre-fix baseline (core.integration.workers ->
core.generation.adapters.comfyui, core.network.service -> core.models.schemas) - the recursive
scan surfaced ZERO previously-invisible intra-subsystem cycles, i.e. this codebase happens to
have none today. Recursion was proven able to both find (planted intra-subsystem cycle,
core/reporting/cycle_a.py <-> cycle_b.py -> FAIL naming it) and not over-fire (untouched tree
-> PASS, same 2-edge baseline) before this was trusted. If a future intra-subsystem cycle is
ever legitimately pre-existing (e.g. inherited from a merge), record it in
import-cycle-baseline.json with a comment marking it a burn-down candidate rather than
silently fixing it or weakening this gate to keep the number small.

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

--- Guard-ordering consistency pass, 2026-08-27 (sibling G1/G2 fix applied here too) ---

This gate's `main()` previously treated a vanished baseline edge as a non-blocking "note" (never
FAILed, never affected the exit code) — unlike G1 (`check_ruff_baseline.py`) and G2
(`check_mypy_baseline.py`), which both FAIL on a vanished finding (2026-08-27 cross-repo
mypy-fail-open fix) because a vanished finding can be either a genuine improvement OR a sign
detection silently stopped covering something it used to cover. The same risk applies here: a
misconfigured `acyclic_siblings` contract (e.g. `ancestors`/`skip_descendants` edited to exclude
a subsystem) would make a real cycle-breaking edge vanish from `current` with no other signal —
the existing `_find_undiscovered_files` fail-safe catches ONLY the "file invisible to grimp"
species of masking, not a narrowed contract.

Standardized onto the SAME shared decision as G1/G2: this gate's own inline baseline-diff logic
(`new = [...]`, `resolved = [...]`) is now handed to `baseline_lib.report_and_decide()` (see that
function's docstring in `lib/baseline.py`, including why an earlier "write anyway, just print a
warning" version was rejected) instead of the old hand-rolled note-vs-FAIL branching — `main()`
here now only owns the G4-specific fail-safe (`undiscovered`, checked and returned FIRST,
unchanged) and building `new`/`resolved` from `_find_cycle_breakers()`'s output; the
report/decide/`--update-baseline` shape itself is byte-identical to G1/G2's, so all three gates
can never drift apart on it again. `--update-baseline` REFUSES to write (exit 1, baseline file
byte-for-byte unchanged) whenever both new and resolved edges are present in the same run —
new-only and resolved-only both still proceed normally.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib import baseline as baseline_lib
from lib.baseline import BaselineCorruptError
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


def _load_contract_options() -> dict:
    """Read this repo's own declared `acyclic_siblings` contract options straight out of
    pyproject.toml's `[[tool.importlinter.contracts]]` block (via import-linter's own
    `read_user_options`), so this script's cycle detection can never silently drift from what
    the contract itself declares - no duplicated `ancestors = ["core"]` literal to keep in
    sync by hand if that block is ever edited (e.g. `skip_descendants` added)."""
    from importlinter import configuration
    from importlinter.application.use_cases import read_user_options

    configuration.configure()
    user_options = read_user_options(config_filename=str(ROOT / "pyproject.toml"))
    for options in user_options.contracts_options:
        if options.get("type") == "acyclic_siblings":
            return {k: v for k, v in options.items() if k not in ("name", "type", "id")}
    raise RuntimeError(
        'No [[tool.importlinter.contracts]] block with type = "acyclic_siblings" found in '
        f"{ROOT / 'pyproject.toml'} - this script mirrors that contract's own declared config "
        "and has nothing to check without it."
    )


def _find_cycle_breakers() -> tuple[list[str], list[str]]:
    import grimp
    from importlinter.contracts.acyclic_siblings import AcyclicSiblingsContract

    sys.path.insert(0, str(ROOT))
    graph = grimp.build_graph(ROOT_PACKAGE)
    undiscovered = _find_undiscovered_files(graph)

    # Use import-linter's own contract class - NOT a hand-rolled walk - so recursion into every
    # descendant package (see module docstring) is import-linter's actual, already-tested
    # algorithm, not a reimplementation of it.
    contract = AcyclicSiblingsContract(
        name="core sibling packages are acyclic",
        session_options={"root_packages": [ROOT_PACKAGE]},
        contract_options=_load_contract_options(),
    )
    check = contract.check(graph, verbose=False)
    cycle_breakers_by_package: dict[str, set[tuple[str, str]]] = check.metadata[
        "cycle_breakers_by_package"
    ]

    # Flatten every recursion level's breakers into one edge set. Identities are the
    # (importer, imported) tuples themselves - canonical and stable regardless of which
    # recursion level found them, and each level only pairs up ITS OWN ancestor's direct
    # children, so the same edge can never be produced by two different levels (no duplicate-
    # per-enclosing-package spam to guard against here).
    edges = {
        f"{importer} -> {imported}"
        for breakers in cycle_breakers_by_package.values()
        for importer, imported in breakers
    }
    return sorted(edges), undiscovered


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

    try:
        baseline = baseline_lib.load(BASELINE_PATH)
    except BaselineCorruptError as e:
        print(f"[G4] FAIL - baseline file problem, refusing to trust this run:\n{e}", file=sys.stderr)
        return 1
    new, resolved = baseline_lib.diff(current, baseline)
    return baseline_lib.report_and_decide(
        gate="G4",
        noun="cycle-breaking edge(s)",
        baseline_path=BASELINE_PATH,
        baseline=baseline,
        current=current,
        new=new,
        resolved=resolved,
        update_mode=update_mode,
    )


if __name__ == "__main__":
    sys.exit(main())
