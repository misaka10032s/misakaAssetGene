# misakaAssetGene — Claude collaboration guide

Desktop-first multimodal asset workbench. Consultant-style dialogue integrates image generation,
character lines, voice, songs, and video — plus downstream LoRA/GPT-SoVITS training pipelines.
Stack: Tauri + Vue 3/Vite/UnoCSS (frontend) · Python/FastAPI (core API) · Ollama (local LLM).

> Cluster conventions (git authority, language, i18n, ports, layout) are BINDING and live at
> D:/backup/CSIA/@PM/.claude/context/cluster-conventions.md — Read it before any work here.

## Delegation & verification

- Orchestration, model tiering, and dispatch rules: D:/backup/CSIA/@PM/.claude/context/model-dispatch-doctrine.md
- Decision rubrics (escalate / done / ask / change course): D:/backup/CSIA/@PM/.claude/context/judgment-rubrics.md
- Whoever produced work never certifies it — verification runs in a fresh-context agent.
- Every done/correct/dead/broken claim carries evidence: file:line, test output, or read-back.
- Target missing or contradicting the task → STOP and ask; never scaffold around it.

## Context index

- `docs/superpowers/specs/spec.md` — **SINGLE SOURCE OF TRUTH (spec-first). MUST read before ANY change; update spec here FIRST, then code.**

## Core principles

1. **Spec-first:** When a new requirement arrives, discuss feasibility, architectural impact, risks, and implementation approach with the `architect` role using `docs/superpowers/specs/spec.md`, then update `docs/superpowers/specs/spec.md` only after confirmation (spec lives at `docs/superpowers/specs/spec.md`).
2. **Plan-aware:** `.plan/DEVELOPMENT_PLAN.md` defines development roles and workflows; `.plan/RESEARCH_LOG.md` records research conclusions and spec amendments. Completed items must be marked as **Done** in the research log.
3. **Repo boundary:** Always treat third-party repos as external dependencies — use an independent clone or download artifacts; they must not be tracked by this project's git; no submodule / subtree.
4. **Multimodal by default:** Feature designs must not assume a single asset output type; must be able to handle composite deliverables including images, character lines, character voices, songs, videos, and animated stills.
5. **Open-source friendly:** Any workflow, spec, and documentation should consider readability, executability, and license clarity for external contributors.
6. **Truthful delivery:** Never describe a skeleton, stub, or PoC as a completed milestone; when reporting, clearly distinguish "Done", "Partially done", and "Not done".

## Work entry points

- Spec discussion: use `.claude/commands/spec-discuss.md`
- Spec sync: use `.claude/commands/update-spec.md`
- Plan review: use `.claude/commands/review-plan.md`

## Rule modules

- `.claude/rules/spec-workflow.md`: standard workflow from requirement to spec
- `.claude/rules/multimodal-assets.md`: composite asset and output design constraints
- `.claude/rules/repo-hygiene.md`: repo boundary, gitignore, and external dependency rules
- `.claude/rules/community-workflow.md`: open-source contribution and review workflow
- `.claude/rules/frontend-standards.md`: frontend i18n, types, RWD, styles, and comment standards

## Ports

All local services bind to `127.0.0.1`; ports are defined centrally in `.env`:

- **Frontend** `http://127.0.0.1:8400`, **Core API** `http://127.0.0.1:8401`, **Ollama** `http://127.0.0.1:11434`

> Note: MisakaAssetGene is a desktop Tauri app (not a browser-delivered web service). The canonical
> browser-testing rules (in `cluster-conventions.md`) apply when verifying the embedded WebView or
> the Vite dev-server URL during development.

## Dev commands

```bash
npm run dev              # Vite dev server (frontend/, port 8400)
npm run dev:core         # FastAPI core via .venv, --reload (port 8401)
npm run start:dev        # both, via scripts/dev_stack.py
npm run build            # production frontend build
npm run typecheck        # vue-tsc --project frontend/tsconfig.json --noEmit
npm test                 # vitest run (frontend/src/**/*.{test,spec}.ts)
npm run test:coverage    # vitest run --coverage

uv sync --extra dev              # install/refresh the Python dev toolchain (.venv)
.venv/Scripts/python -m pytest -q         # run the Python test suite (tests/)
```

## Code quality gates

Hybrid repo — Vue/TS frontend (`frontend/`) and Python core (`core/`, `tests/`) each get their
own gate family. Config/thresholds/baselines live in normal tool locations
(`package.json`, `pyproject.toml`, `quality-gates/`), never under `.claude/`. Two tiers per
stack: **L0** (seconds-level, hook-enforced) and **L1** (L0 + diff coverage [+ mutation on the
JS/TS side]).

```bash
# JS/TS (frontend/) — from repo root
npm run gate:g1   # eslint, diff-LINE-scoped (880 pre-existing warnings on the whole tree —
                   # see below; this gate only fails on NEW warnings/errors on changed lines)
npm run gate:g2   # vue-tsc --project frontend/tsconfig.json --noEmit, baselined (0 pre-existing)
npm run gate:g3   # vitest run + assertion-presence on new/changed test files
npm run gate:g4   # madge import-cycle check, baselined (0 pre-existing cycles)
npm run gate:g5   # vitest run --coverage + diff-coverage.mjs (>=60% of changed lines)
npm run gate:g6   # Stryker mutation testing, scoped to the diff's changed line ranges
npm run gate:l0   # g1+g2+g3+g4 — exits 0 on the untouched tree (~11s)
npm run gate:l1   # l0+g5+g6   — exits 0 on the untouched tree (~11s, 0 test files today)

# Python (core/, tests/) — from repo root, using the repo's own uv-managed .venv
.venv/Scripts/python quality-gates/python/run.py g1   # ruff check ., baselined (180 identities, 269 raw)
.venv/Scripts/python quality-gates/python/run.py g2   # mypy core --strict, baselined (50 identities, 123 raw)
.venv/Scripts/python quality-gates/python/run.py g3   # pytest -q + AST assertion-presence on new/changed tests
.venv/Scripts/python quality-gates/python/run.py g4   # import-linter acyclic_siblings, baselined (2 pre-existing edges)
.venv/Scripts/python quality-gates/python/run.py g5   # pytest --cov=core --cov-report=xml + diff-cover (>=60%)
.venv/Scripts/python quality-gates/python/run.py l0   # g1+g2+g3+g4 — exits 0 on the untouched tree (~16s)
.venv/Scripts/python quality-gates/python/run.py l1   # l0+g5        — exits 0 on the untouched tree (~33s)

# after a deliberate, reviewed fix/cleanup (or knowingly accepting a new pre-existing item) —
# re-snapshots CURRENT findings as the new baseline; never a bypass for work still in progress
npm run gate:g2:update-baseline
npm run gate:g4:update-baseline
.venv/Scripts/python quality-gates/python/run.py g1 --update-baseline
.venv/Scripts/python quality-gates/python/run.py g2 --update-baseline
.venv/Scripts/python quality-gates/python/run.py g4 --update-baseline
```

- **Pre-commit hook** (`.githooks/pre-commit`) runs ONLY the L0 of whichever stack(s) the
  commit actually touches (staged-file-list based: `frontend/*` -> JS/TS `gate:l0`;
  `core/*`/`tests/*`/`scripts/*`/`pyproject.toml` -> Python `quality-gates/python/run.py l0`).
  **Not installed by default** — activate once per clone/machine with:
  `git config core.hooksPath .githooks`
- **G1 lint is diff-LINE-scoped, not `--max-warnings=0`** — `frontend/src/**` carries a real
  pre-existing backlog (880 ESLint warnings, measured 2026-08-27, almost all Vue formatting
  rules: `vue/singleline-html-element-content-newline`, `vue/max-attributes-per-line`,
  `vue/html-*`). A repo-wide zero-warnings gate would fail on day one for every contributor
  regardless of what they touched, so this gate lints changed files but only fails on messages
  whose line is inside the diff's changed lines (same model misaka_site2.0's
  `check-lint-diff.mjs` uses). ESLint config: `eslint.config.mjs` (repo root — this repo has no
  `frontend/package.json` of its own, so it lives beside the root `package.json` like every
  other build config here; scoped to `frontend/src/**` only via `files`/`ignores`).
- **G2 typecheck is NOT vacuous here** (unlike misaka_site2.0's solution-style root tsconfig
  case) — canary-proven 2026-08-27: `frontend/tsconfig.json` is a normal leaf config
  (`include: [...]`, no `files: []`/project-reference shape), and a planted
  `const x: number = "not a number"` in `frontend/src/main.ts` was caught by
  `vue-tsc --project frontend/tsconfig.json --noEmit` (TS2322, exit 2) and reverted. Baseline
  is currently EMPTY (0 pre-existing errors) — kept as a version-controlled mechanism anyway so
  the shape matches every other baselined gate here.
- **G3(b) assertion-presence uses the TS-capable parser fix** (`languageOptions.parser:
  tseslint.parser`) that misaka_site2.0's `check-test-assertions.mjs` documents: without it, a
  typed `.test.ts` file parse-errors under the default `espree` parser and a zero-assertion
  block silently passes. Re-verified on this repo (2026-08-27): a typed helper function inside
  a planted zero-assertion test was caught, not silently skipped.
- **G4 (Python) uses import-linter's `acyclic_siblings` contract** (`[tool.importlinter]` in
  `pyproject.toml`, `ancestors=["core"]`), NOT a hand-authored `layers` contract — `core/` is
  ~13 peer subsystems (consultant, editor, generation, integration, llm, memory, models,
  network, project, reporting, scheduler, training, + main.py/config.py) with no single strict
  dependency order across them, so an artificial layer ordering would be wrong on day one.
  `acyclic_siblings` checks the same "no cycle between siblings" invariant the JS-family recipe
  gets from madge, recursively at every nesting depth. `quality-gates/python/check_import_cycles.py`
  calls `grimp.build_graph('core').nominate_cycle_breakers('core')` directly — the identical
  algorithm the pyproject.toml contract declares, just consumed as a stable API instead of
  parsed CLI prose. Baseline: 2 pre-existing edges (`core.integration.workers ->
  core.generation.adapters.comfyui`, `core.network.service -> core.models.schemas`).
- **G5 (Python) diff-coverage vacuity — fixed 2026-08-27, fresh-reviewer finding.** A
  brand-new `core/**/*.py` file that nothing imports used to be entirely ABSENT from
  `coverage.xml` (not 0% — just missing), so `diff-cover` reported "No lines with coverage
  information in this diff" and exited 0/PASS for genuinely untested new code. Root cause,
  confirmed empirically: coverage.py's unexecuted-file discovery needs `[tool.coverage.run]
  source = ["core"]` (now set) AND is itself a `pkgutil`-style PACKAGE walk that silently
  skips any directory with no `__init__.py` (an implicit PEP 420 namespace package) —
  `core/reporting/` was exactly such a directory (the only one under `core/`, now fixed with
  an added empty `__init__.py`). Neither fix alone was sufficient on this repo; both were
  required (verified by testing each independently). On top of both, `diff_coverage.py` ALSO
  carries its own independent, narrower fail-safe check
  (`_find_unmeasured_changed_files`/`_measured_files_in_coverage_xml`): any changed
  `core/**/*.py` file with 1+ changed lines and ZERO entries in `coverage.xml` is a hard FAIL
  naming the file, regardless of whether the `source=`/`__init__.py` mechanism catches it —
  belt-and-braces against a FUTURE namespace-package directory reintroducing the same gap.
  Proven independently: with `core/reporting/__init__.py` temporarily removed again, a
  same-shape orphan file was still caught by this second layer alone (exit 1, named).
- **G4 (Python) shared the identical namespace-package blind spot** — found while diagnosing
  G5 above. `grimp.build_graph('core')` is the SAME kind of `pkgutil` package walk; with
  `core/reporting/__init__.py` missing, `core.reporting.license` was entirely invisible to the
  cycle-detection graph (0 `core.reporting.*` modules discovered), meaning a cycle involving
  that file could never have been flagged. Now fixed as a side effect of the same
  `__init__.py` addition, PLUS `check_import_cycles.py` gained its own equivalent fail-safe
  (`_find_undiscovered_files`): every `core/**/*.py` file on disk must have a matching entry
  in grimp's module list, or the gate FAILs naming the undiscovered file(s) rather than
  silently reporting "no cycles found" on an incomplete graph.
- **Subprocess-crash blindness — fixed 2026-08-27, fresh-reviewer finding.** `ruff`/`mypy`/
  `vue-tsc` all failed loud with a non-zero return code AND empty/unparsable stdout when
  pointed at a nonexistent config file — but `check_ruff_baseline.py`, `check_mypy_baseline.py`,
  and `check-typecheck-baseline.mjs` (G1/G2 both stacks) never checked the subprocess return
  code, so `json.loads(stdout or "[]")` / a regex over empty output silently became "0
  findings" and printed PASS even though the tool never actually ran. Reproduced for all
  three (nonexistent `--config`/`--config-file`/`--project` path) before the fix; all three
  now check the tool's own documented exit codes (ruff/mypy: 0=clean, 1=findings, anything
  else=crash; vue-tsc on this repo: 0=clean, 2=diagnostics found, 1=crash — verified
  empirically, NOT assumed to match ruff/mypy's ordering) via
  `quality-gates/python/lib/tool_run.py`'s `run_and_check` (Python) or an equivalent inline
  check (JS), and FAIL loud naming the crash instead of parsing whatever partial output
  exists. **Audited and found NOT vulnerable**: `check_import_cycles.py` (calls grimp's
  Python API in-process, so a crash is an uncaught exception — already loud by construction,
  confirmed by pointing it at a nonexistent root package); `check-lint-diff.mjs` /
  `check-test-assertions.mjs` (in-process ESLint API, confirmed via a broken
  `eslint.config.mjs` syntax error crashing loud); `check-import-cycles.mjs` (in-process
  madge API, confirmed via a nonexistent `tsConfig` path crashing loud);
  `mutation-diff.mjs`/G6 (already checks the mutation REPORT FILE's existence explicitly,
  not just Stryker's exit code); `diff-coverage.mjs`/G5 JS (no subprocess of its own — chained
  via `&&` after `vitest run --coverage`, so a vitest crash already short-circuits before this
  script runs).
- **G2 (Python/mypy) fail-OPEN on a broken/missing config — fixed 2026-08-27, cross-repo
  investigation** (`D:/backup/CSIA/@PM/state/runs/CROSS-REPO-mypy-failopen.md`). The
  subprocess-crash guard above does NOT catch this: unlike ruff (which exits 2 on a broken
  `pyproject.toml` and is caught by `ok_returncodes`), mypy **silently falls back to its own
  defaults** on a broken/missing config — losing `strict`/`disallow_untyped_defs` — and still
  exits 0 or 1 (a normal "ran" code), just with fewer findings. Reproduced on this repo
  2026-08-27: a syntactically broken `[tool.mypy` (missing `]`) and a `pyproject.toml` moved
  away entirely both dropped the raw finding count from 123 to 41, silently vanishing 21 of the
  50 baselined identities, while `[G2]` still printed PASS (the vanished findings were only an
  informational "note", never a failure). Ruff was tested the same way (broken `[tool.ruff`,
  and a separate unrecognized-key case) and both correctly exit 2 with empty stdout, already
  caught by the existing crash guard — the hole is mypy-specific on this repo. Fixed with two
  parts in `check_mypy_baseline.py`:
  1. **Pre-flight config validation** (`_validate_mypy_config`) — before trusting any mypy
     output, confirms `pyproject.toml` exists, parses via the stdlib `tomllib`, and its
     `[tool.mypy]` table carries `strict` + `disallow_untyped_defs`; any failure raises
     `MypyConfigError` naming the exact parse error, not a generic "crashed" message. mypy is
     now invoked with an explicit `--config-file pyproject.toml` (bare relative name, not an
     absolute path) so mypy's own config-diagnostic lines on **stderr** are prefixed with that
     exact bare string (verified: `pyproject.toml: [mypy]: Unrecognized option: ...`,
     `pyproject.toml: Expected ']' at the end of a table declaration ...`) — an absolute
     `--config-file` would make mypy echo the full path instead, breaking a simple
     prefix/substring check. After the subprocess returns, stderr is scanned (non-anchored
     `re.search`, defense in depth) for that prefix; a match raises `MypyConfigError` even when
     mypy's own exit code was a normal 0/1 — this is what catches an unrecognized single option
     under an otherwise-valid `[tool.mypy]` table (mypy applies the REST of a syntactically
     valid table and only warns — finding count unchanged in that specific case, but the
     warning itself is still the loud, named signal now).
  2. **A vanished baseline finding is now a FAILURE, not a note** — durable half of the fix,
     because it catches ANY future mechanism that silently disables the strict profile, not
     just a broken TOML file. `check_ruff_baseline.py` got the identical treatment for
     consistency (ns-media-hub's sibling investigation found a real ruff-side vacuity there via
     the same masked-crash shape — exit 2, empty stdout, coerced to `"[]"` — this repo's own
     ruff is not vulnerable to that specific mechanism, verified above, but the vanished-finding
     guard is applied regardless).
  **Legitimately shrinking a baseline now requires an explicit, deliberate step**: fix the
  code, run the gate (it FAILs, naming exactly which baselined finding(s) vanished), confirm
  the improvement is real, then re-run with `--update-baseline` to re-snapshot. A baseline
  shrinking silently (gate still prints PASS) is no longer possible by design — that silence is
  exactly what the original defect looked like. Proven end-to-end on this repo: fixing one real
  mypy finding (`core/training/service.py` — added `[object, ...]` type args to a bare `tuple`
  annotation) made `[G2]` FAIL naming it; `--update-baseline` then dropped it from 50 to 49 and
  the gate went green. Same round-trip proven for ruff (one unused import removed, 180 → 179).
  A genuinely NEW finding (a planted `x: int = "not a number"` canary) still FAILs exactly as
  before, on both gates.
- **`--update-baseline` REFUSES on a mixed new+resolved run — fixed 2026-08-27, sibling-repo
  reviewer finding.** A DIFFERENT fail-open from the one above: the reporting/exit-code path in
  G1/G2/G4's `main()` was never vulnerable to a sibling repo's exact defect (evaluating a
  vanished-baseline branch and returning before ever checking for NEW findings) — a plain run on
  this repo already reports both `new` and `resolved` before exiting 1 (verified by inspection
  and by reproduction). The REAL hole was one level down: `--update-baseline` re-snapshotted
  `current` **unconditionally**, with no diff shown at all. Reproduced here 2026-08-27: fixing
  one real baselined finding (e.g. `core/generation/adapters/ace_step.py`'s bare `dict` return
  type) while simultaneously planting one genuinely new, unrelated finding made a plain run FAIL
  naming both — but then running `--update-baseline`, exactly as the FAIL message's own advice
  suggested, silently absorbed the new finding into the baseline too (baseline count unchanged:
  −1 resolved, +1 new — no output named what had just been accepted). Fixed by extracting one
  shared decision, `quality-gates/python/lib/baseline.py`'s `report_and_decide()`, used
  identically by G1 (`check_ruff_baseline.py`), G2 (`check_mypy_baseline.py`) and G4
  (`check_import_cycles.py`) so the three gates cannot drift apart on this again:
  `--update-baseline` now **REFUSES to write** (exit 1, every new AND resolved finding printed
  by name, the baseline file left byte-for-byte unchanged — hash-verified) whenever both sets are
  non-empty in the same run. A new-only run (deliberately accepting a finding as debt) and a
  resolved-only run (a pure shrink) both still proceed normally, now naming every finding they
  accept or remove instead of writing silently. An earlier version of this fix let
  `--update-baseline` write anyway while merely printing a warning — rejected, because an
  announcement that still exits 0 does not stop a scripted or muscle-memory
  `--update-baseline && git commit` chain from absorbing the new finding regardless. Proven for
  all three gates: (a) new-only → FAIL naming it, `--update-baseline` proceeds and names what it
  accepts; (b) resolved-only → FAIL naming it with the `--update-baseline` remedy instruction,
  `--update-baseline` proceeds and names what it removes; (c) both at once → FAIL naming BOTH,
  and `--update-baseline` REFUSES (exit 1, baseline file hash unchanged — confirmed via
  `git hash-object`); (d) neither → PASS. Also fixed alongside: a corrupt (non-JSON, or
  JSON-but-not-an-array) baseline file used to raise an uncaught `json.JSONDecodeError`
  traceback — it already failed loud (non-zero exit), so never a fail-open, but
  `baseline_lib.BaselineCorruptError` now gives every gate a clean, named `[G_] FAIL` message
  instead of a raw traceback.
- **Non-blocking DX note, fixed cheaply** — a bare `node quality-gates/frontend/<script>.mjs`
  run from inside `frontend/` used to crash with a cryptic internal stack trace (no
  `frontend/package.json` exists, so paths computed relative to the wrong root). Every JS/TS
  gate script now calls `assertRepoRoot(cwd)` (`lib/git-diff.mjs`) first and fails with a
  clear message pointing at the supported invocation (`npm run gate:<name>`) instead. The
  documented interface (`npm run gate:X`) was always correct and is unaffected.
- **G6 (mutation testing) is REMOVED for the Python side, cluster-wide** (see
  `D:/backup/CSIA/@PM/.claude/context/cluster-conventions.md` and every other managed repo's
  own quality-gates doc) — `mutmut` 3.x refuses to run on native Windows at all ("To run mutmut
  on Windows, please use the WSL."), exit code 1, unconditionally, before mutating anything.
  Not attempted here. The JS/TS side DOES carry G6 (Stryker, `stryker.config.mjs` at repo
  root) — scoped to `frontend/src/**/*.ts` only (no maintained Vue-SFC mutator, so `.vue`
  component script blocks are a real, reported scope gap, not an oversight).
- **Scratch/generated dirs are explicitly excluded from every gate's scope** — never relied on
  a tool's default scan (a stale scratch test under a gitignored `tmp/` must never be able to
  block a commit). Proven 2026-08-27: identical violation/failing-test/zero-assertion content
  planted under `frontend/tmp/` (JS) or `tmp/` (Python, plus pytest's own `testpaths =
  ["tests"]` scoping) was invisible to every gate (exit 0); the SAME content staged under
  `frontend/src/` (JS) or `core/`/`tests/` (Python) was caught (exit 1) by the matching gate.
  `[tool.ruff] extend-exclude` and `[tool.mypy] exclude` in `pyproject.toml`, and
  `vitest.config.ts`'s `test.exclude`/`eslint.config.mjs`'s `ignores`, all name these
  directories explicitly rather than trusting a default.
- **G5/G3(a) "tests green" on the JS/TS side is currently a no-op tier** — 0 `.test.ts` files
  exist under `frontend/src/` today (measured 2026-08-27). `vitest.config.ts` sets
  `test.passWithNoTests: true` so this is a documented, honest pass rather than a false-red
  install defect; the first real test file added flips gate:g3/g5/g6 back to normal
  pass/fail behavior automatically (all three were exercised via a temporary canary
  file+test during the proof-of-failure pass, reverted afterward — see git history on
  `feat/quality-gates`).
- Identity keys for every baseline (never a bare count): JS `file:line:col:code` (G2) or
  `file|ruleId|message` (G1, diff-scoped so this rarely matters); Python
  `relative/file.py|CODE|message` (G1/G2, line number excluded so unrelated edits don't shift
  identities) and `importer -> imported` module pairs (G4).

## Dev mode and diagnostic standards

1. **Diagnostic output during development must be controlled by mode / env.**
   - Python backend uses `MISAKA_ENV=dev`
   - Frontend / Vite uses `VITE_MISAKA_ENV=dev` and `--mode development`
   - Production builds must not output development debug messages by default
2. **Build and dev must be isolated.**
   - dev server, typecheck, build, doctor, and manager must each have a clearly defined command entry point
   - When verifying, state whether it is a dev verification, build verification, or API/behavior verification
3. **Development messages serve only verification purposes and must not pollute the end-user experience.**
4. **When adding diagnostic output, simultaneously document the launch method, expected output, and disable condition.**
5. **Env naming segregation:** backend reads `MISAKA_*` and provider secrets; frontend reads only `VITE_MISAKA_*`.

## Report format (must be included with every development progress report)

1. **Current progress:** corresponding `docs/superpowers/specs/spec.md` / milestone / item
2. **How to verify:** command, page, API, expected output
3. **Current assessment:** Done / Partially done / Not done
4. **Next step:** the next most reasonable development or acceptance action

For milestone acceptance, additionally list:
- Which items passed
- Which items are still missing
- Which are only scaffold / stub

## Role assignments

| Role | Primary responsibilities |
| --- | --- |
| `architect` | Requirement feasibility, system layering, spec gatekeeping |
| `backend` | FastAPI core, file system, project management, metadata |
| `ai-ml` | RAG, prompt engineering, LLM routing, generation/training workflows |
| `frontend` | Tauri/Vue UI, version tree, asset browsing and interaction |
| `ui-ux` | Dialogue experience, visual hierarchy, onboarding and usability |
| `devops` | Setup, packaging, cross-platform installation, tool and worker management |
| `qa-sdet` | Smoke / integration / E2E test strategy |
| `security` | Permission boundaries, command safety, sensitive data sanitization |

See `.claude/agents/` for detailed personas.
