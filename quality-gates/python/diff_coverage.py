#!/usr/bin/env python
"""G5 - diff coverage >= threshold (60% default, changed lines only).

Thin wrapper around `diff-cover` (reads the Cobertura XML `pytest-cov` already produces),
ported from D:/backup/CSIA/.WEB/learningMachine/api/quality-gates/diff_coverage.py (verified
recipe), adjusted for this repo's coverage target (`core`) and main branch (`main`), PLUS an
independent fail-safe check (see `_find_unmeasured_changed_files` below) added 2026-08-27
after a fresh-reviewer-found vacuity.

Must run AFTER `pytest --cov=core --cov-report=xml` (see quality-gates/python/run.py's g5
command) - reads coverage.xml, does not generate it.

--- The vacuity this file now guards against (fresh-reviewer finding, 2026-08-27; root cause
    CORRECTED after independent re-verification the same day) ---

coverage.xml only lists a `<class filename="...">` entry for a source file if coverage.py's
tracer actually saw it get imported/executed DURING the test run, UNLESS coverage.py is also
given the scope of the package to walk at report time (add unexecuted files as explicit
0%-covered entries) — via EITHER `[tool.coverage.run] source = ["core"]` in pyproject.toml
(now set) OR a `--cov=core` CLI flag (this repo's `run.py` g5 command already passes one).
Re-verified directly (2026-08-27): with the pyproject.toml `source` line disabled but the CLI
flag and `__init__.py` both intact, an unimported/untested file still showed up in
coverage.xml at 0% and this gate still failed correctly — so on THIS repo the `source` line is
redundant with the CLI flag already in use, not a second necessary ingredient.

What neither scope declaration can do on its own is enter a directory with no `__init__.py` —
that walk is itself a `pkgutil`-style PACKAGE walk, so it silently skips any directory that is
an implicit PEP 420 namespace package — `core/reporting/` was exactly such a directory (now
fixed) and is why the reviewer's repro landed there specifically, independent of which scope
declaration was or wasn't present. Re-verified directly the other way too: with `source`
restored but `__init__.py` removed again, a fresh staged unimported file was invisible to
coverage.xml and only this file's own belt-and-braces check below caught it (native diff-cover
alone did not, exit 0/PASS from diff-cover; exit 1/FAIL only once this file's guard ran).

Either way — missing scope config, a future namespace-package directory, or any other
reason a file never reaches coverage.py's radar — the OLD behavior for a completely-absent
file was: diff-cover finds zero `<class>` rows to compare its diff lines against, prints "No
lines with coverage information in this diff", and exits 0 (PASS). A brand-new, real,
uncovered .py file therefore sailed through untested. This is fail-OPEN: "I have no data" was
silently treated as "nothing to worry about". Do not assume a `source=`/`--cov` scope
declaration, alone or combined, makes this vacuity impossible — a missing `__init__.py` breaks
BOTH, which is exactly why the guard below cross-checks the diff directly against
coverage.xml's actual contents instead of trusting any upstream discovery mechanism.

`_find_unmeasured_changed_files` closes this independently of whatever coverage.py's own
file-discovery manages: it cross-references the diff's changed lines directly against
coverage.xml's own `<class filename>` entries (not trusting diff-cover's interpretation of
"no data"). Any core/**/*.py file with 1+ changed lines that has ZERO entries in coverage.xml
is treated as a hard FAIL, named explicitly — regardless of `source` config, package
structure, or any other reason coverage.py might not have seen it. This makes the gate
fail-SAFE: a file this gate cannot see is treated as unproven, never as fine.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib.git_diff import (
    ensure_utf8_stdio,
    get_changed_files,
    get_changed_line_ranges,
    resolve_base_ref,
)

ensure_utf8_stdio()

ROOT = Path(__file__).resolve().parent.parent.parent  # repo root
COVERAGE_XML = ROOT / "coverage.xml"
THRESHOLD = os.environ.get("QUALITY_DIFF_COVERAGE_THRESHOLD", "60")
TEST_FILE_PREFIXES = ("test_",)
TEST_FILE_SUFFIXES = ("_test.py",)
SCOPE_PREFIX = "core/"


def _is_test_file(rel_path: str) -> bool:
    name = rel_path.rsplit("/", 1)[-1]
    return name.startswith(TEST_FILE_PREFIXES) or name.endswith(TEST_FILE_SUFFIXES)


def _measured_files_in_coverage_xml(coverage_xml: Path) -> set[str]:
    """Every file coverage.xml has ANY <class filename="..."> entry for, resolved to a path
    relative to ROOT (posix separators) — regardless of whether that file shows 0% or 100%.
    Absence from this set means coverage.py never even discovered the file, which is the
    exact condition `_find_unmeasured_changed_files` treats as unproven, not "fine"."""
    tree = ET.parse(coverage_xml)
    root_el = tree.getroot()

    source_roots = [Path(s.text.strip()) for s in root_el.findall("./sources/source") if s.text]
    if not source_roots:
        source_roots = [ROOT]

    measured: set[str] = set()
    for class_el in root_el.findall(".//class"):
        filename = class_el.get("filename")
        if not filename:
            continue
        filename_path = Path(filename.replace("\\", "/"))
        resolved = None
        for src in source_roots:
            candidate = (src / filename_path).resolve()
            # Prefer a source root under which the file actually exists on disk (handles the
            # multi-<source> case correctly); fall back to the first root if none match, so a
            # relocated/renamed source root still yields a best-effort path rather than
            # silently dropping the entry.
            if candidate.exists():
                resolved = candidate
                break
        if resolved is None:
            resolved = (source_roots[0] / filename_path).resolve()
        try:
            rel = resolved.relative_to(ROOT).as_posix()
        except ValueError:
            continue  # outside the repo entirely — not something git diff could reference anyway
        measured.add(rel)
    return measured


def _find_unmeasured_changed_files(base_ref: str, coverage_xml: Path) -> list[tuple[str, int]]:
    """Returns [(file, changed_line_count), ...] for every changed core/**/*.py file (non-test)
    that has 1+ changed lines but ZERO entries in coverage.xml — i.e. coverage.py never even
    discovered it, so diff-cover has nothing to compare and would otherwise report "no lines
    with coverage information" and silently pass."""
    changed = [
        f
        for f in get_changed_files(ROOT, base_ref, ["py"])
        if f.startswith(SCOPE_PREFIX) and not _is_test_file(f)
    ]
    if not changed:
        return []

    changed_lines = get_changed_line_ranges(ROOT, base_ref, changed)
    measured = _measured_files_in_coverage_xml(coverage_xml)

    unmeasured = []
    for f in changed:
        lines = changed_lines.get(f, set())
        if not lines:
            continue  # pure deletion / rename-only — nothing new to require coverage for
        if f not in measured:
            unmeasured.append((f, len(lines)))
    return sorted(unmeasured)


def main() -> int:
    base_ref = resolve_base_ref(ROOT)

    if not COVERAGE_XML.exists():
        print(
            f"[G5] FAIL - {COVERAGE_XML} not found. Run "
            '"pytest --cov=core --cov-report=xml" first (quality-gates/python/run.py g5 does this for you).',
            file=sys.stderr,
        )
        return 1

    # --- fail-safe check FIRST: a file coverage.xml has never even heard of is never "fine" ---
    unmeasured = _find_unmeasured_changed_files(base_ref, COVERAGE_XML)
    if unmeasured:
        print(
            f"[G5] FAIL - {len(unmeasured)} changed file(s) under {SCOPE_PREFIX} have ZERO "
            "entries in coverage.xml (coverage.py never discovered them - likely never "
            "imported by anything the test suite exercises). Treating every changed line in "
            "them as uncovered rather than silently passing:",
            file=sys.stderr,
        )
        for f, n in unmeasured:
            print(f"  - {f} ({n} changed line(s), 0 measured)", file=sys.stderr)
        print(
            "\n[G5] Add a test that imports/exercises the file above (or wire it into "
            "something that already is), then re-run.",
            file=sys.stderr,
        )
        return 1

    # Resolve diff-cover from the SAME interpreter's own Scripts/bin dir first (this repo's
    # pinned .venv copy) rather than trusting whatever `diff-cover` shutil.which() finds first
    # on PATH — a stray global install with a different version would silently diverge from
    # the pinned quality-gates deps otherwise.
    venv_scripts = Path(sys.executable).parent
    candidate = venv_scripts / ("diff-cover.exe" if sys.platform == "win32" else "diff-cover")
    diff_cover = str(candidate) if candidate.exists() else shutil.which("diff-cover")
    if diff_cover is None:
        print("[G5] FAIL - diff-cover not installed (uv sync --extra dev).", file=sys.stderr)
        return 1

    cmd = [
        diff_cover,
        str(COVERAGE_XML),
        f"--compare-branch={base_ref}",
        f"--fail-under={THRESHOLD}",
        "--include-untracked",
        "--show-uncovered",
    ]
    print(f"[G5] $ {' '.join(cmd)} (cwd={ROOT})")
    result = subprocess.run(cmd, cwd=ROOT)
    if result.returncode != 0:
        print(f"\n[G5] FAIL - diff coverage below {THRESHOLD}% vs {base_ref}.", file=sys.stderr)
        return result.returncode
    print(f"\n[G5] PASS - diff coverage >= {THRESHOLD}% vs {base_ref}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
