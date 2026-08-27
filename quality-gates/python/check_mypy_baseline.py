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

--- Fail-OPEN fix, two parts (added 2026-08-27, cross-repo defect — see
D:/backup/CSIA/@PM/state/runs/CROSS-REPO-mypy-failopen.md) ---

The exit-code guard above does NOT catch every fail-open path: unlike ruff (which exits 2 and
is caught by `ok_returncodes` for BOTH a syntactically broken `pyproject.toml` and an
unrecognized `[tool.ruff]` key — verified empirically on this repo 2026-08-27), a
syntactically-broken or missing `pyproject.toml` makes mypy **silently fall back to its own
defaults** — losing `strict`/`disallow_untyped_defs` and every strict-only diagnostic — print
nothing to stdout about it, and still exit 0 or 1 (a normal "ran and found N errors" code) with
a genuinely SMALLER error count. Reproduced on this repo 2026-08-27: a syntactically broken
`[tool.mypy` (missing `]`) dropped the raw finding count from 123 to 41 and 21 of the 50
baselined identities silently vanished; `pyproject.toml` renamed away entirely reproduced the
identical 41/21 numbers (both exit 1, not 0 — this repo's variant of the defect is "fewer
findings", not literally "zero findings", but the effect on the gate is the same: PASS while
the strict profile is not actually being enforced). A merely-unrecognized single key inside an
otherwise-valid `[tool.mypy]` table (`not_a_real_mypy_option`) did NOT change the finding count
here — mypy applies the rest of a syntactically valid table and only warns on stderr about the
one bad key — but that warning is still a signal worth failing on (see part B below), because a
future mypy version or a different kind of "recognized but wrong" key could change that.

Part A — validate the config before trusting the run (`_validate_mypy_config`):
  1. Confirm `pyproject.toml` exists at the repo root.
  2. Parse it ourselves with the stdlib `tomllib` (Python 3.11+, same parser family mypy's own
     TOML backend uses) — a `TOMLDecodeError` here means mypy would ALSO fail to honour it
     (verified: the identical broken-table edit that reproduces the defect is also invalid TOML
     by `tomllib`'s own parser).
  3. Confirm `[tool.mypy]` exists and carries the specific keys this gate's strictness depends
     on (`strict`, `disallow_untyped_defs`) — catches an accidental deletion of the whole
     section even when the surrounding TOML is otherwise well-formed.
  Any failure here raises `MypyConfigError` with the exact parse error / missing-key detail —
  loud, before a single mypy finding is trusted.

  Additionally, mypy is invoked with an EXPLICIT `--config-file pyproject.toml` (bare relative
  name, not resolved to an absolute path) so its own config-related stderr diagnostics are
  prefixed with that exact bare string — verified empirically (`pyproject.toml: [mypy]:
  Unrecognized option: ...`, `pyproject.toml: Expected ']' at the end of a table declaration
  ...`) — rather than relying on mypy's directory-upward auto-discovery, whose target file name
  is not guaranteed stable across mypy versions/invocations. After the subprocess returns
  (returncode still 0/1, i.e. "mypy did not crash" by `run_and_check`'s own definition), stderr
  is scanned for any line matching that prefix; a match means mypy itself flagged a config
  problem even though it kept running — also raises `MypyConfigError`, naming the line(s)
  verbatim. This is the layer that catches the "unrecognized option, count unchanged" case
  above, which Part A's own `tomllib` pre-check cannot see (that config IS valid TOML with all
  required keys present — mypy is the one complaining about the specific option, not the parser).
  The stderr scan uses `re.search` (not `str.startswith`), non-anchored, precisely because an
  absolute `--config-file` would make mypy echo the FULL PATH rather than the bare filename in
  its diagnostic — a sibling repo's fix hit this; keeping the bare relative name sidesteps it,
  and `re.search` is defense in depth if that ever changes. This only inspects **stderr**, never
  stdout — every legitimate finding line lives in stdout and is parsed via `json.loads` first
  (see `_run_mypy` below), so there is no risk of a finding whose message text happens to start
  with "pyproject.toml:" being misread as a config diagnostic.

Part B — treat a vanished baseline finding as a FAILURE, not an informational note (`main`):
  A baselined finding disappearing is either a genuine improvement (fix it forward via
  `--update-baseline`) or a sign the strict profile silently stopped applying — as demonstrated
  above, both a broken config AND (in other repos) a full stdout-suppressing crash surface
  through this exact symptom. The gate no longer passes silently on this: it FAILs, names every
  vanished identity (never the whole baseline — this baseline runs to 50 identities and dumping
  all of them would bury the signal), and tells the developer to re-run with
  `--update-baseline` if the improvement is real. This is the durable half of the fix: it
  catches ANY future mechanism that makes findings vanish, not just a broken/missing TOML file.
"""
from __future__ import annotations

import json
import re
import sys
import tomllib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib import baseline as baseline_lib
from lib.git_diff import ensure_utf8_stdio
from lib.tool_run import ToolCrashedError, run_and_check

ensure_utf8_stdio()

ROOT = Path(__file__).resolve().parent.parent.parent  # repo root
BASELINE_PATH = Path(__file__).resolve().parent / "mypy-baseline.json"
CONFIG_PATH = ROOT / "pyproject.toml"
CONFIG_FILE_ARG = "pyproject.toml"  # bare relative name — see Part A docstring above
# Keys this gate's strictness guarantee actually depends on; missing any of these means mypy
# would NOT be running the profile this baseline was recorded against.
REQUIRED_MYPY_KEYS = ("strict", "disallow_untyped_defs")
# Non-anchored: an absolute --config-file would make mypy echo the full path instead of the
# bare name, so this deliberately does not assume the diagnostic starts at column 0.
_CONFIG_DIAGNOSTIC_RE = re.compile(re.escape(CONFIG_FILE_ARG) + r":")


class MypyConfigError(RuntimeError):
    """Raised when pyproject.toml is missing/unparsable/incomplete, or when mypy itself
    reported a config-parse problem on stderr despite otherwise exiting 0/1 — i.e. the run
    happened but did NOT fully honour this repo's strict profile, so its findings cannot be
    trusted as a real strict-mode result."""


def _validate_mypy_config() -> None:
    if not CONFIG_PATH.exists():
        raise MypyConfigError(
            f"{CONFIG_FILE_ARG} does not exist at {CONFIG_PATH} — mypy silently falls back to "
            "its own (non-strict) defaults instead of crashing when its config file is "
            "missing, and would still report a normal exit code. Restore the file before "
            "trusting a G2 run."
        )
    try:
        data = tomllib.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as e:
        raise MypyConfigError(
            f"{CONFIG_FILE_ARG} is not valid TOML — mypy falls back to defaults on a config "
            f"parse failure rather than crashing, so this gate refuses to trust the run "
            f"until the file parses again. Parse error: {e}"
        ) from e
    mypy_cfg = data.get("tool", {}).get("mypy")
    if not mypy_cfg:
        raise MypyConfigError(
            f"[tool.mypy] table is missing from {CONFIG_FILE_ARG} — mypy would run under its "
            "own defaults, not this repo's strict profile, and still report a normal exit code."
        )
    missing = [k for k in REQUIRED_MYPY_KEYS if k not in mypy_cfg]
    if missing:
        raise MypyConfigError(
            f"[tool.mypy] in {CONFIG_FILE_ARG} is missing required key(s) {missing} that this "
            "gate's strict guarantee depends on — restore them before trusting a G2 run."
        )


def _run_mypy() -> list[dict]:
    _validate_mypy_config()
    proc = run_and_check(
        [sys.executable, "-m", "mypy", "core", "--config-file", CONFIG_FILE_ARG, "--output=json"],
        cwd=ROOT,
        ok_returncodes=(0, 1),  # 0 = clean, 1 = errors found — anything else is a crash
    )
    # mypy can exit 0/1 ("ran") while still telling us, on stderr, that it could not fully
    # honour pyproject.toml (e.g. an unrecognized option under [tool.mypy]) — that is a
    # config problem, never a benign side note. See Part A docstring above.
    config_lines = [
        line for line in proc.stderr.splitlines() if _CONFIG_DIAGNOSTIC_RE.search(line)
    ]
    if config_lines:
        raise MypyConfigError(
            "mypy reported a config problem on stderr instead of fully honouring "
            f"{CONFIG_FILE_ARG} — refusing to trust this run as a real strict-mode result:\n"
            + "\n".join(config_lines)
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
    except MypyConfigError as e:
        print(f"[G2] FAIL - mypy config problem, refusing to trust this run:\n{e}", file=sys.stderr)
        return 1
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
            f"[G2] FAIL - {len(resolved)} previously-baselined mypy error(s) no longer exist:",
            file=sys.stderr,
        )
        for v in resolved:
            print(f"  - {v}", file=sys.stderr)
        print(
            "\nA vanished baseline finding means either a genuine improvement, or that the "
            "strict type-check profile silently stopped applying (broken/missing config, a "
            "crashed subprocess masked as '0 findings', etc). This gate does not pass "
            "silently on that ambiguity. If the improvement is real, re-run with "
            "--update-baseline to shrink the baseline; otherwise investigate why these "
            "errors vanished before trusting the tree.",
            file=sys.stderr,
        )

    if new:
        print(f"[G2] FAIL - {len(new)} NEW mypy error(s) not present in the baseline:", file=sys.stderr)
        for v in new:
            print(f"  - {v}", file=sys.stderr)

    if new or resolved:
        print(
            f"\nBaseline: {BASELINE_PATH.name} ({len(baseline)} pre-existing error(s)).",
            file=sys.stderr,
        )
        return 1

    print(f"[G2] PASS - {len(current)} total error(s), 0 new vs baseline ({len(baseline)} pre-existing).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
