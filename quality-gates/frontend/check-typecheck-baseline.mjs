#!/usr/bin/env node
// G2 — "no NEW vue-tsc error vs a version-controlled baseline."
//
// Confirmed NOT vacuous on this repo (canary-proven 2026-08-27): frontend/tsconfig.json is a
// normal leaf tsconfig (`include: ["src/**/*.ts", "src/**/*.vue", "vite.config.ts"]`, no
// `files: []` / project-reference solution-style shape — the vacuous case documented in
// misaka_site2.0's cluster-conventions.md `## Testing timing`). A planted
// `const x: number = "not a number"` in frontend/src/main.ts was caught (TS2322, exit code 2)
// and reverted; the same command exits 0 on the untouched tree. So this gate uses
// `vue-tsc --project frontend/tsconfig.json --noEmit` directly (this repo's own `npm run
// typecheck` script) rather than the `--build` workaround misaka_site2.0 needed.
//
// Baseline is currently EMPTY (0 pre-existing errors, measured 2026-08-27) — kept as a real,
// version-controlled diffing mechanism anyway (not a bare pass/fail) so a future contributor
// who introduces debt on purpose still has a documented, reviewable path via
// --update-baseline, and so this script's shape matches the Python-family G2 exactly.
//
// Identity = file:line:col:code (message text excluded — TS wording can drift across compiler
// versions without the underlying error changing).
//
// --- Subprocess-crash fail-safe (added 2026-08-27, fresh-reviewer finding) ---
//
// vue-tsc's own exit codes on this repo (empirically verified 2026-08-27, NOT the same
// ordering as ruff/mypy's 0/1/2 — do not assume, check): 0 = clean, 2 = diagnostics
// (type errors) reported, 1 = the compiler itself failed to run (bad --project path, invalid
// args — never actually typechecked anything). This script used to build `output` from
// stdout+stderr unconditionally and regex-parse it for `file(line,col): error TSxxxx:` lines
// with NO check of `result.status` at all. A crash message ("error TS5058: The specified path
// does not exist: ...") does not match that per-diagnostic regex shape, so `parseErrors`
// silently returned `[]` and the gate printed PASS — reproduced 2026-08-27 by pointing
// `--project` at a nonexistent tsconfig path (exit 1, 0 errors parsed, false PASS).
import { spawnSync } from 'node:child_process'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { assertRepoRoot } from './lib/git-diff.mjs'

const cwd = process.cwd()
assertRepoRoot(cwd)
const __dirname = path.dirname(fileURLToPath(import.meta.url))
const BASELINE_PATH = path.join(__dirname, 'typecheck-baseline.json')

const ERROR_RE = /^([^\s(][^(]*\.(?:ts|tsx|vue))\((\d+),(\d+)\): error (TS\d+): (.*)$/
const OK_EXIT_CODES = new Set([0, 2]) // 0 = clean, 2 = diagnostics found — 1 (or anything else) = crash

class ToolCrashedError extends Error {}

function runTypecheck() {
  const result = spawnSync('npx', ['vue-tsc', '--project', 'frontend/tsconfig.json', '--noEmit'], {
    cwd,
    encoding: 'utf-8',
    shell: true, // Windows: .cmd shims (npx.cmd) fail with EINVAL under shell:false
    maxBuffer: 64 * 1024 * 1024,
  })
  const output = (result.stdout || '') + (result.stderr || '')
  if (!OK_EXIT_CODES.has(result.status)) {
    throw new ToolCrashedError(
      `vue-tsc exited ${result.status} (expected one of ${[...OK_EXIT_CODES].join(', ')}) — ` +
        `treating this as a CRASH, not '0 errors'.\n--- output ---\n${output}`,
    )
  }
  return output
}

function parseErrors(output) {
  const errors = []
  for (const line of output.split('\n')) {
    const m = ERROR_RE.exec(line.trim())
    if (!m) continue
    const [, file, lineNo, col, code] = m
    errors.push({
      file: file.replace(/\\/g, '/'),
      line: Number(lineNo),
      col: Number(col),
      code,
    })
  }
  return errors
}

function identity(e) {
  return `${e.file}:${e.line}:${e.col}:${e.code}`
}

function loadBaseline() {
  if (!fs.existsSync(BASELINE_PATH)) return { errors: [] }
  return JSON.parse(fs.readFileSync(BASELINE_PATH, 'utf-8'))
}

function main() {
  const updateMode = process.argv.includes('--update-baseline')
  let output
  try {
    output = runTypecheck()
  } catch (err) {
    if (err instanceof ToolCrashedError) {
      console.error(`[G2] FAIL — vue-tsc crashed instead of running cleanly:\n${err.message}`)
      return 1
    }
    throw err
  }
  const current = parseErrors(output)

  if (updateMode) {
    const files = [...new Set(current.map((e) => e.file))].sort()
    const payload = {
      generated: new Date().toISOString().slice(0, 10),
      count: current.length,
      files,
      errors: current.map(identity).sort(),
    }
    fs.writeFileSync(BASELINE_PATH, JSON.stringify(payload, null, 2) + '\n')
    console.log(`[G2] baseline updated — ${current.length} error(s) across ${files.length} file(s) recorded at ${path.relative(cwd, BASELINE_PATH)}.`)
    return 0
  }

  const baseline = loadBaseline()
  const baselineSet = new Set(baseline.errors || [])
  const currentIdentities = current.map(identity)
  const currentSet = new Set(currentIdentities)

  const newErrors = current.filter((e) => !baselineSet.has(identity(e)))
  const resolvedCount = (baseline.errors || []).filter((id) => !currentSet.has(id)).length

  if (resolvedCount > 0) {
    console.log(`[G2] note: ${resolvedCount} baseline error(s) no longer present — consider --update-baseline to shrink it.`)
  }

  if (newErrors.length > 0) {
    console.error(`[G2] FAIL — ${newErrors.length} NEW vue-tsc error(s) not present in the baseline (${baseline.count ?? (baseline.errors || []).length} pre-existing):`)
    for (const e of newErrors) {
      console.error(`  - ${e.file}:${e.line}:${e.col} ${e.code}`)
    }
    console.error(`\nBaseline: ${path.relative(cwd, BASELINE_PATH)}. If these errors are intentional/expected, re-run with --update-baseline after review.`)
    return 1
  }

  console.log(`[G2] PASS — ${current.length} total error(s), 0 new vs baseline (${baseline.count ?? (baseline.errors || []).length} pre-existing).`)
  return 0
}

try {
  process.exit(main())
} catch (err) {
  console.error('[G2] gate crashed:', err)
  process.exit(1)
}
