# misakaAssetGene — Claude collaboration guide

Desktop-first multimodal asset workbench; consultant-style dialogue integrates image generation, character lines, voice, songs, and video — plus downstream LoRA/GPT-SoVITS training pipelines; stack: Tauri + Vue 3/Vite/UnoCSS (frontend) · Python/FastAPI (core API) · Ollama (local LLM).

> Cluster rules: `D:/backup/CSIA/@PM/.claude/CLAUDE.md` `## Before doing X, read Y`.

## Context index

- `docs/superpowers/specs/spec.md` — **SINGLE SOURCE OF TRUTH (spec-first).** **MUST read before ANY change; update spec here FIRST, then code.**

## Core principles

1. **Spec-first:** When a new requirement arrives, discuss feasibility, architectural impact, risks, and implementation approach with the `architect` role using `docs/superpowers/specs/spec.md`, then update `docs/superpowers/specs/spec.md` only after confirmation (spec lives at `docs/superpowers/specs/spec.md`).
2. **Plan-aware:** `.plan/DEVELOPMENT_PLAN.md` defines development roles and workflows; `.plan/RESEARCH_LOG.md` records research conclusions and spec amendments; completed items must be marked as **Done** in the research log.
3. **Repo boundary:** Always treat third-party repos as external dependencies — use an independent clone or download artifacts; they must not be tracked by this project's git; no submodule / subtree.
4. **Multimodal by default:** Feature designs must not assume a single asset output type; must be able to handle composite deliverables including images, character lines, character voices, songs, videos, and animated stills.
5. **Open-source friendly:** Any workflow, spec, and documentation should consider readability, executability, and license clarity for external contributors.
6. **Truthful delivery:** Never describe a skeleton, stub, or PoC as a completed milestone; when reporting, clearly distinguish "Done", "Partially done", and "Not done".

## Work entry points

- Spec discussion: use `.claude/commands/spec-discuss.md`
- Spec sync: use `.claude/commands/update-spec.md`
- Plan review: use `.claude/commands/review-plan.md`

## Rule files

| File | Read when |
|---|---|
| `.claude/rules/spec-workflow.md` | a new requirement arrives, or the spec, roles, workflow or repo rules change (spec-first workflow) |
| `.claude/rules/multimodal-assets.md` | designing any feature's asset or output types (multimodal asset rules) |
| `.claude/rules/repo-hygiene.md` | touching the repo boundary, `.gitignore`, external dependencies or personal settings files (repo boundary and hygiene rules) |
| `.claude/rules/community-workflow.md` | preparing a contribution, a PR, or a change involving a license, third-party repo or model source (open-source community collaboration rules) |
| `.claude/rules/frontend-standards.md` | any frontend work: i18n, types, RWD, styles, comments (frontend implementation standards) |

## Ports

All local services bind to `127.0.0.1`; ports are defined centrally in `.env`:

- **Frontend** `http://127.0.0.1:8400`, **Core API** `http://127.0.0.1:8401`, **Ollama** `http://127.0.0.1:11434`

> Note: MisakaAssetGene is a desktop Tauri app (not a browser-delivered web service).
>
> How its embedded WebView and Vite dev-server page are judged: `D:/backup/CSIA/@PM/.claude/context/cluster-conventions.md` `## Testing timing` ("No screen tests").

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

Hybrid repo — Vue/TS frontend (`frontend/`) and Python core (`core/`, `tests/`) each get their own gate family; config/thresholds/baselines live in normal tool locations (`package.json`, `pyproject.toml`, `quality-gates/`), never under `.claude/`; two tiers per stack: **L0** (seconds-level, hook-enforced) and **L1** (L0 + diff coverage [+ mutation on the JS/TS side]).

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
npm run gate:l1   # l0+g5+g6   — exits 0 on the untouched tree (14 test files)

# Python (core/, tests/) — from repo root, using the repo's own uv-managed .venv
.venv/Scripts/python quality-gates/python/run.py g1   # ruff check ., baselined (177 identities, 278 raw)
.venv/Scripts/python quality-gates/python/run.py g2   # mypy core --strict, baselined (50 identities, 124 raw)
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

- **Pre-commit hook** (`.githooks/pre-commit`) runs ONLY the L0 of whichever stack(s) the commit actually touches (staged-file-list based: `frontend/*` -> JS/TS `gate:l0`; `core/*`/`tests/*`/`scripts/*`/`pyproject.toml` -> Python `quality-gates/python/run.py l0`); hook path: `git config core.hooksPath .githooks`, run once per clone; it is not self-installing, and a detached HEAD or `git commit --no-verify` skips it (`D:/backup/CSIA/@PM/.claude/context/cluster-conventions.md` `### Hook carrier (L0 enforcement)`).

**Repo-specific gate rules (evidence-based; full incident history: `docs/superpowers/decisions/2026-09-09-quality-gate-history.md`):**

- **G1 (ESLint) lints only diff-changed lines**, never repo-wide `--max-warnings=0` — `frontend/src/**` carries 880 pre-existing ESLint warnings (almost all Vue formatting rules: `vue/singleline-html-element-content-newline`, `vue/max-attributes-per-line`, `vue/html-*`); ESLint config: `eslint.config.mjs` (repo root — this repo has no `frontend/package.json` of its own, so it lives beside the root `package.json`; scoped to `frontend/src/**` only via `files`/`ignores`).
- **G2 typecheck is NOT vacuous here** — `frontend/tsconfig.json` is a normal leaf config (`include: [...]`, no `files: []`/project-reference shape); baseline is EMPTY (0 pre-existing errors).
- **G3(b) assertion-presence requires the TS-capable parser** (`languageOptions.parser: tseslint.parser`) — without it, a typed `.test.ts` file parse-errors under the default `espree` parser and a zero-assertion block silently passes.
- **G4 (Python) uses import-linter's `acyclic_siblings` contract** (`[tool.importlinter]` in `pyproject.toml`, `ancestors=["core"]`), NOT a hand-authored `layers` contract — `core/` is ~13 peer subsystems with no single strict dependency order across them; `quality-gates/python/check_import_cycles.py` calls `grimp.build_graph('core').nominate_cycle_breakers('core')` directly; baseline: 2 pre-existing edges (`core.integration.workers -> core.generation.adapters.comfyui`, `core.network.service -> core.models.schemas`).
- **G5 (Python) diff-coverage requires `[tool.coverage.run] source = ["core"]` AND every `core/**` subdirectory to carry `__init__.py`** (`core/reporting/__init__.py`) — coverage.py's unexecuted-file discovery is a `pkgutil`-style package walk that silently skips a directory with no `__init__.py`; `diff_coverage.py` additionally carries its own fail-safe (`_find_unmeasured_changed_files`): any changed `core/**/*.py` file with 1+ changed lines and ZERO entries in `coverage.xml` is a hard FAIL naming the file.
- **G4 (Python import-linter) shares the same namespace-package requirement as G5 diff coverage** — `grimp.build_graph('core')` is the same kind of package walk; `check_import_cycles.py` carries the matching fail-safe (`_find_undiscovered_files`): every `core/**/*.py` file on disk must have a matching entry in grimp's module list, or the gate FAILs naming the undiscovered file(s).
- **Every gate checks the tool's own exit code before trusting its output** (`quality-gates/python/lib/tool_run.py`'s `run_and_check` for Python, an equivalent inline check for JS): ruff/mypy `0`=clean, `1`=findings, anything else=crash; vue-tsc on this repo `0`=clean, `2`=diagnostics found, `1`=crash; **Prohibited:** parsing a subprocess's stdout/stderr as a finding list without first checking its exit code against this table.
- **G2 (Python/mypy) validates its own config before trusting any output** — `check_mypy_baseline.py`'s `_validate_mypy_config` confirms `pyproject.toml` parses and its `[tool.mypy]` table carries `strict` + `disallow_untyped_defs`; mypy is invoked with `--config-file pyproject.toml` (bare relative name — required so mypy's own config-error lines on stderr are prefixed with that exact string for a substring check).
- **A vanished baseline finding is a FAILURE, not an informational note** — applies to G1 (ruff), G2 (mypy), AND G4 (import-linter); legitimately shrinking a baseline requires an explicit `--update-baseline` step after confirming the improvement is real; a baseline cannot shrink silently while the gate still prints PASS.
- **`--update-baseline` REFUSES to write (exit 1, baseline file unchanged) whenever a run has BOTH new AND resolved findings at once** — for G1 ruff, G2 mypy, and G4 import-linter alike, via the shared `quality-gates/python/lib/baseline.py`'s `report_and_decide()`; a new-only run (deliberately accepting debt) or a resolved-only run (a pure shrink) both proceed normally, naming every finding accepted or removed; a corrupt (non-JSON, or JSON-but-not-an-array) baseline file raises `baseline_lib.BaselineCorruptError` with a named `[G_] FAIL`, never a silent bypass.
- **G6 diff mutation: none for Python — @PM cluster-conventions `## Code quality gates (ADR-030)`, "Python family: no G6 diff mutation".** The JS/TS side DOES carry G6 (Stryker, `stryker.config.mjs` at repo root) — scoped to `frontend/src/**/*.ts` only (no maintained Vue-SFC mutator, so `.vue` component script blocks are a real, reported scope gap, not an oversight).
- **Scratch/generated dirs are explicitly excluded from every gate's scope**, never relying on a tool's default scan: `[tool.ruff] extend-exclude` and `[tool.mypy] exclude` in `pyproject.toml`; `vitest.config.ts`'s `test.exclude`; `eslint.config.mjs`'s `ignores`.
- **G3 tests / G5 diff coverage on the JS/TS side exercise real tests** — 14 `.test.ts` files / 98 tests exist under `frontend/src/`; `vitest.config.ts` still sets `test.passWithNoTests: true` so an empty diff never false-reds, but `gate:g3`/`g5`/`g6` are exercising real pass/fail behavior.
- Identity keys for every baseline (never a bare count): JS `file:line:col:code` (G2 typecheck) or `file|ruleId|message` (G1 lint, diff-scoped so this rarely matters); Python `relative/file.py|CODE|message` (G1 ruff / G2 mypy, line number excluded so unrelated edits don't shift identities) and `importer -> imported` module pairs (G4 import-linter).

## Dev mode and diagnostic standards

1. **Diagnostic output during development must be controlled by mode / env.** Python backend uses `MISAKA_ENV=dev`; frontend / Vite uses `VITE_MISAKA_ENV=dev` and `--mode development`; production builds must not output development debug messages by default
2. **Build and dev must be isolated.** dev server, typecheck, build, doctor, and manager must each have a clearly defined command entry point; when verifying, state whether it is a dev verification, build verification, or API/behavior verification
3. **Development messages serve only verification purposes and must not pollute the end-user experience.**
4. **When adding diagnostic output, simultaneously document the launch method, expected output, and disable condition.**
5. **Env naming segregation:** backend reads `MISAKA_*` and provider secrets; frontend reads only `VITE_MISAKA_*`.

## Report file format (the format of every subagent's report file for a development task)

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
| `qa-sdet` | Smoke / integration / E2E (command- or API-level flows; never a browser-launching test) test strategy |
| `security` | Permission boundaries, command safety, sensitive data sanitization |

See `.claude/agents/` for detailed personas.
