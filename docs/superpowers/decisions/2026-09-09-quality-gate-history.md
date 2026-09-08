# 2026-09-09 — Quality-gate rollout incident history (moved out of `.claude/CLAUDE.md`)

## 背景

站主要求（待回答 #56）：規則檔（`.claude/CLAUDE.md` 等 agent 讀取的規則檔）只留「指令／baseline
數字／禁令」，覆盤敘事（事故重現、除錯過程、量測證據、「為什麼這樣修」的故事）一律搬到 `docs/`，只
搬不刪。同一套處理已先在 misaka_site2.0 做過示範（`2026-09-08-quality-gate-history.md`），本檔
是 misakaAssetGene 的同款搬移。misakaAssetGene 的 `.claude/CLAUDE.md`（342 行）中，「## Code
quality gates」一節（約第 70-303 行）近半是 quality-gate 安裝過程的事故敘事。

本檔案收容原 `.claude/CLAUDE.md` 中所有屬於「NARRATIVE」分類的段落，逐節標注原出處（section +
行號），內容為**逐字搬移、內容不變**，非摘要改寫。規則檔中對應位置留下一行 back-pointer 指回這裡。

---

## 原 `.claude/CLAUDE.md` §Code quality gates → G1 diff-scoped deviation (舊版第 113-121 行)

> 原文位置：`.claude/CLAUDE.md` 舊版第 113-121 行

- **G1 lint is diff-LINE-scoped, not `--max-warnings=0`** — `frontend/src/**` carries a real
  pre-existing backlog (880 ESLint warnings, measured 2026-08-27, almost all Vue formatting
  rules: `vue/singleline-html-element-content-newline`, `vue/max-attributes-per-line`,
  `vue/html-*`). A repo-wide zero-warnings gate would fail on day one for every contributor
  regardless of what they touched, so this gate lints changed files but only fails on messages
  whose line is inside the diff's changed lines (same model misaka_site2.0's
  `check-lint-diff.mjs` uses). ESLint config: `eslint.config.mjs` (repo root — this repo has no
  `frontend/package.json` of its own, so it lives beside the root `package.json` like every
  other build config here; scoped to `frontend/src/**` only via `files`/`ignores`).

(操作性內容——G1 只 lint diff 變更行、880 筆 pre-existing warning baseline、ESLint config 位置與
scope——仍保留於 `.claude/CLAUDE.md`；此處只是原文逐字保存。880 筆數字已於 2026-09-09 重新量測，
仍為 880，未過期。)

## 原 `.claude/CLAUDE.md` §Code quality gates → G2 typecheck not vacuous (舊版第 122-128 行)

> 原文位置：`.claude/CLAUDE.md` 舊版第 122-128 行

- **G2 typecheck is NOT vacuous here** (unlike misaka_site2.0's solution-style root tsconfig
  case) — canary-proven 2026-08-27: `frontend/tsconfig.json` is a normal leaf config
  (`include: [...]`, no `files: []`/project-reference shape), and a planted
  `const x: number = "not a number"` in `frontend/src/main.ts` was caught by
  `vue-tsc --project frontend/tsconfig.json --noEmit` (TS2322, exit 2) and reverted. Baseline
  is currently EMPTY (0 pre-existing errors) — kept as a version-controlled mechanism anyway so
  the shape matches every other baselined gate here.

(操作性內容——G2 非空洞 gate、baseline 目前為 0——仍保留於 `.claude/CLAUDE.md`；此處保存 canary
證明過程的逐字原文。2026-09-09 重新執行 `gate:g2` 確認仍為 0 pre-existing error，未過期。)

## 原 `.claude/CLAUDE.md` §Code quality gates → G3(b) TS-capable parser fix (舊版第 129-133 行)

> 原文位置：`.claude/CLAUDE.md` 舊版第 129-133 行

- **G3(b) assertion-presence uses the TS-capable parser fix** (`languageOptions.parser:
  tseslint.parser`) that misaka_site2.0's `check-test-assertions.mjs` documents: without it, a
  typed `.test.ts` file parse-errors under the default `espree` parser and a zero-assertion
  block silently passes. Re-verified on this repo (2026-08-27): a typed helper function inside
  a planted zero-assertion test was caught, not silently skipped.

(操作性內容——G3(b) 必須使用 `tseslint.parser`，否則型別化的零斷言測試會被靜默放行——仍保留於
`.claude/CLAUDE.md`；此處保存原文的複核細節。)

## 原 `.claude/CLAUDE.md` §Code quality gates → G4 acyclic_siblings rationale (舊版第 134-144 行)

> 原文位置：`.claude/CLAUDE.md` 舊版第 134-144 行

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

(操作性內容——G4 用 `acyclic_siblings`、baseline 2 條 edge 及其名稱——仍保留於 `.claude/CLAUDE.md`；
此處保存選型理由的逐字原文。2026-09-09 重新執行 `gate:g4`（Python）確認仍為 2 條、0 new，未過期。)

## 原 `.claude/CLAUDE.md` §Code quality gates → G5 diff-coverage vacuity fix (舊版第 145-161 行)

> 原文位置：`.claude/CLAUDE.md` 舊版第 145-161 行，"fixed 2026-08-27, fresh-reviewer finding"

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

(操作性內容——`source = ["core"]`、`core/reporting/__init__.py` 為必要條件、
`_find_unmeasured_changed_files` 的 fail-safe 機制——仍保留於 `.claude/CLAUDE.md`；此處保存根因
調查與雙重驗證的逐字原文。2026-09-09 重新確認 `core/reporting/__init__.py` 仍存在，未過期。)

## 原 `.claude/CLAUDE.md` §Code quality gates → G4 shares the namespace-package blind spot (舊版第 162-170 行)

> 原文位置：`.claude/CLAUDE.md` 舊版第 162-170 行

- **G4 (Python) shared the identical namespace-package blind spot** — found while diagnosing
  G5 above. `grimp.build_graph('core')` is the SAME kind of `pkgutil` package walk; with
  `core/reporting/__init__.py` missing, `core.reporting.license` was entirely invisible to the
  cycle-detection graph (0 `core.reporting.*` modules discovered), meaning a cycle involving
  that file could never have been flagged. Now fixed as a side effect of the same
  `__init__.py` addition, PLUS `check_import_cycles.py` gained its own equivalent fail-safe
  (`_find_undiscovered_files`): every `core/**/*.py` file on disk must have a matching entry
  in grimp's module list, or the gate FAILs naming the undiscovered file(s) rather than
  silently reporting "no cycles found" on an incomplete graph.

(操作性內容——`check_import_cycles.py` 的 `_find_undiscovered_files` fail-safe——仍保留於
`.claude/CLAUDE.md`；此處保存原文。)

## 原 `.claude/CLAUDE.md` §Code quality gates → Subprocess-crash blindness fix (舊版第 171-192 行)

> 原文位置：`.claude/CLAUDE.md` 舊版第 171-192 行，"fixed 2026-08-27, fresh-reviewer finding"

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

(操作性內容——每個 gate 呼叫子行程前先檢查退出碼、退出碼對照表——仍保留於 `.claude/CLAUDE.md`；此
處保存重現過程與稽核清單的逐字原文。)

## 原 `.claude/CLAUDE.md` §Code quality gates → G2 mypy fail-open fix (舊版第 193-237 行)

> 原文位置：`.claude/CLAUDE.md` 舊版第 193-237 行，"fixed 2026-08-27, cross-repo investigation"

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

(操作性內容——`_validate_mypy_config` 的必要 key、`--config-file pyproject.toml` 必須是相對路徑、
「vanished baseline = FAILURE」政策、合法縮小 baseline 的正確流程——仍保留於 `.claude/CLAUDE.md`；
此處保存根因調查與端到端驗證的逐字原文。注意本段引用的 ruff 數字「180 → 179」「123」為 2026-08-27
當時的量測，2026-09-09 重新量測後 ruff 現為 177 identities / 278 raw、mypy 為 50 identities / 124
raw——見 `.claude/CLAUDE.md` 目前 gate 指令表格的即時數字，此歷史檔的數字僅為當時記錄，不再更新。)

## 原 `.claude/CLAUDE.md` §Code quality gates → `--update-baseline` refuses on mixed run (舊版第 238-269 行)

> 原文位置：`.claude/CLAUDE.md` 舊版第 238-269 行，"fixed 2026-08-27, sibling-repo reviewer finding"

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

(操作性內容——`--update-baseline` 在 new+resolved 同時出現時拒絕寫入、`BaselineCorruptError`
的命名 FAIL 訊息——仍保留於 `.claude/CLAUDE.md`；此處保存重現與四態驗證的逐字原文。)

## 原 `.claude/CLAUDE.md` §Code quality gates → Non-blocking DX note (舊版第 270-275 行)

> 原文位置：`.claude/CLAUDE.md` 舊版第 270-275 行

- **Non-blocking DX note, fixed cheaply** — a bare `node quality-gates/frontend/<script>.mjs`
  run from inside `frontend/` used to crash with a cryptic internal stack trace (no
  `frontend/package.json` exists, so paths computed relative to the wrong root). Every JS/TS
  gate script now calls `assertRepoRoot(cwd)` (`lib/git-diff.mjs`) first and fails with a
  clear message pointing at the supported invocation (`npm run gate:<name>`) instead. The
  documented interface (`npm run gate:X`) was always correct and is unaffected.

（此段 100% 為已修復的 DX 小插曲記錄，無仍需遵守的獨立指令內容——`.claude/CLAUDE.md` 的 gate 指令
表格本就已寫明正確呼叫方式 `npm run gate:X`。整段搬移，未在規則檔留額外條目。）

## 原 `.claude/CLAUDE.md` §Code quality gates → G6 removed for Python side (舊版第 276-282 行)

> 原文位置：`.claude/CLAUDE.md` 舊版第 276-282 行

- **G6 (mutation testing) is REMOVED for the Python side, cluster-wide** (see
  `D:/backup/CSIA/@PM/.claude/context/cluster-conventions.md` and every other managed repo's
  own quality-gates doc) — `mutmut` 3.x refuses to run on native Windows at all ("To run mutmut
  on Windows, please use the WSL."), exit code 1, unconditionally, before mutating anything.
  Not attempted here. The JS/TS side DOES carry G6 (Stryker, `stryker.config.mjs` at repo
  root) — scoped to `frontend/src/**/*.ts` only (no maintained Vue-SFC mutator, so `.vue`
  component script blocks are a real, reported scope gap, not an oversight).

(操作性內容——Python 側 G6 移除、JS/TS 側 G6 範圍限縮於 `frontend/src/**/*.ts`——仍保留於
`.claude/CLAUDE.md`；此處保存 mutmut 在 Windows 上失敗的原始錯誤訊息與退出碼證據，該段原先在瘦身
時被誤刪、未搬到任何地方——2026-09-09 mag-review.md 發現的 MINOR finding，現補回逐字原文。)

## 原 `.claude/CLAUDE.md` §Code quality gates → Scratch/generated dirs excluded (舊版第 283-291 行)

> 原文位置：`.claude/CLAUDE.md` 舊版第 283-291 行

- **Scratch/generated dirs are explicitly excluded from every gate's scope** — never relied on
  a tool's default scan (a stale scratch test under a gitignored `tmp/` must never be able to
  block a commit). Proven 2026-08-27: identical violation/failing-test/zero-assertion content
  planted under `frontend/tmp/` (JS) or `tmp/` (Python, plus pytest's own `testpaths =
  ["tests"]` scoping) was invisible to every gate (exit 0); the SAME content staged under
  `frontend/src/` (JS) or `core/`/`tests/` (Python) was caught (exit 1) by the matching gate.
  `[tool.ruff] extend-exclude` and `[tool.mypy] exclude` in `pyproject.toml`, and
  `vitest.config.ts`'s `test.exclude`/`eslint.config.mjs`'s `ignores`, all name these
  directories explicitly rather than trusting a default.

(操作性內容——四個排除設定的位置與存在——仍保留於 `.claude/CLAUDE.md`；此處保存
plant/reproduce 驗證過程的逐字原文。2026-09-09 重新核對這四個設定鍵仍然存在且內容一致，未過期。)

## 原 `.claude/CLAUDE.md` §Code quality gates → G5/G3(a) no-op tier (舊版第 292-298 行，已過期修正)

> 原文位置：`.claude/CLAUDE.md` 舊版第 292-298 行

- **G5/G3(a) "tests green" on the JS/TS side is currently a no-op tier** — 0 `.test.ts` files
  exist under `frontend/src/` today (measured 2026-08-27). `vitest.config.ts` sets
  `test.passWithNoTests: true` so this is a documented, honest pass rather than a false-red
  install defect; the first real test file added flips gate:g3/g5/g6 back to normal
  pass/fail behavior automatically (all three were exercised via a temporary canary
  file+test during the proof-of-failure pass, reverted afterward — see git history on
  `feat/quality-gates`).

**此段已過期，2026-09-09 修正**：`frontend/src/` 現有 14 個 `.test.ts` 檔案、98 個測試，
`npm run gate:g3` 即時重跑輸出「Test Files 14 passed (14)｜Tests 98 passed (98)」——「0 test files」
「no-op tier」的敘述不再成立。`.claude/CLAUDE.md` 已改寫為反映目前真實狀態（見該檔「G3/G5 on the
JS/TS side now exercise real tests」一條）；`passWithNoTests: true` 的設定本身未變、仍為避免空
diff 誤判紅燈的保險機制，但已不是「目前無任何真測試」的說明。這是過期敘述被下一個功能悄悄超車的
典型案例——canary 證明本身（temporary canary file+test 的操作紀錄）仍具歷史價值，保留於此。

---

Recorded: 2026-09-09 (待回答 #56 rule-file slimming rollout, misakaAssetGene).
