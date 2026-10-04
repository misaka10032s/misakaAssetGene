#!/usr/bin/env python
"""The test files a commit has to run: the staged test files, plus every test file that imports (statically, by
module path) a staged module. Used by `run.py commit`, so the commit step runs only these tests and never the whole
suite (the whole suite is `run.py l0`, the end-of-task run).

A test file is a file under pyproject.toml's `[tool.pytest.ini_options]` `testpaths` whose name matches its
`python_files` (pytest's defaults when a key is absent). Imports are read with `ast`, so `import a.b`,
`from a import b` and `from a.b import c` all count, wherever in the file they stand. A staged module is named by its
dotted path from the repo root (`core/training/service.py` is `core.training.service`, `core/training/__init__.py` is
`core.training` and also covers every `core.training.*` import) and, when its folder has no `__init__.py`, by its
bare file name (which pytest puts on the path for the tests beside it).

Usage (from the repo root):  .venv/Scripts/python quality-gates/python/related_tests.py   (prints the files)
"""
from __future__ import annotations

import ast
import fnmatch
import sys
import tomllib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib.git_diff import ensure_utf8_stdio, get_staged_files

ROOT = Path(__file__).resolve().parent.parent.parent  # repo root


def _pytest_settings(root: Path) -> tuple[list[str], list[str]]:
    """(testpaths, python_files) from pyproject.toml; pytest's own defaults when a key is absent."""
    with (root / "pyproject.toml").open("rb") as handle:
        options = tomllib.load(handle).get("tool", {}).get("pytest", {}).get("ini_options", {})
    testpaths = options.get("testpaths", ["."])
    patterns = options.get("python_files", ["test_*.py", "*_test.py"])
    return list(testpaths), list(patterns)


def all_test_files(root: Path) -> list[str]:
    """Every test file pytest collects here, as paths relative to `root`."""
    testpaths, patterns = _pytest_settings(root)
    found: set[str] = set()
    for testpath in testpaths:
        base = root / testpath
        for path in base.rglob("*.py"):
            if any(fnmatch.fnmatch(path.name, pattern) for pattern in patterns):
                found.add(path.relative_to(root).as_posix())
    return sorted(found)


def module_names(root: Path, rel: str) -> tuple[set[str], bool]:
    """(names the module can be imported by, whether it is a package `__init__`)."""
    parts = rel[: -len(".py")].split("/")
    is_package = parts[-1] == "__init__"
    dotted_parts = parts[:-1] if is_package else parts
    names: set[str] = set()
    if dotted_parts and all(part.isidentifier() for part in dotted_parts):
        names.add(".".join(dotted_parts))
    if not is_package and not (root / Path(*parts[:-1]) / "__init__.py").exists():
        names.add(parts[-1])  # a bare module on a test folder's own path
    return names, is_package


def imported_names(path: Path) -> set[str]:
    """Every module name the file imports, plus `package.name` for each `from package import name`."""
    names: set[str] = set()
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names.add(node.module)
            names.update(f"{node.module}.{alias.name}" for alias in node.names)
    return names


def related_test_files(root: Path = ROOT) -> list[str]:
    """Staged test files plus the test files that import a staged module, relative to `root`, sorted."""
    staged = get_staged_files(root, ["py"])
    tests = all_test_files(root)
    related = {rel for rel in staged if rel in tests}
    exact: set[str] = set()
    packages: set[str] = set()
    for rel in staged:
        names, is_package = module_names(root, rel)
        exact |= names
        if is_package:
            packages |= names
    if exact:
        for rel in tests:
            if rel in related:
                continue
            for name in imported_names(root / rel):
                if name in exact or any(name.startswith(package + ".") for package in packages):
                    related.add(rel)
                    break
    return sorted(related)


def main() -> int:
    ensure_utf8_stdio()
    for rel in related_test_files():
        print(rel)
    return 0


if __name__ == "__main__":
    sys.exit(main())
