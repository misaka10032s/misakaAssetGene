#!/usr/bin/env node
// G6 — surviving mutants in the diff, listed individually (never a percentage — see
// D:/backup/CSIA/.WEB/misaka_site2.0/frontend/quality-gates/mutation-diff.mjs, the verified
// recipe this is ported from, for the full rationale). Scope: frontend/src/**/*.ts only —
// Stryker has no maintained Vue-SFC mutator, so .vue component script blocks are NOT covered.
//
// Runs Stryker scoped to exactly the diff's changed LINE RANGES via Stryker's
// `file:startLine-endLine` --mutate syntax — runtime scales with diff size, not repo size.
import { spawnSync } from 'node:child_process'
import fs from 'node:fs'
import path from 'node:path'
import { getChangedFiles, getChangedLineRanges, resolveBaseRef, assertRepoRoot } from './lib/git-diff.mjs'

const cwd = process.cwd()
assertRepoRoot(cwd)
const TEST_FILE_RE = /\.(test|spec)\.[cm]?[jt]sx?$/
const CONFIG_FILE_RE = /\.config\.[cm]?[jt]s$/
const SCOPE_PREFIX = 'frontend/src/'
const REPORT_PATH = path.join(cwd, 'reports', 'mutation', 'mutation.json')
const NOT_KILLED = new Set(['Survived', 'NoCoverage'])

function toRanges(lineSet) {
  const lines = [...lineSet].sort((a, b) => a - b)
  const ranges = []
  for (const ln of lines) {
    const last = ranges[ranges.length - 1]
    if (last && ln === last.end + 1) last.end = ln
    else ranges.push({ start: ln, end: ln })
  }
  return ranges
}

async function main() {
  const baseRef = resolveBaseRef(cwd)
  const changedFiles = getChangedFiles(cwd, baseRef, ['ts']).filter(
    (f) => f.startsWith(SCOPE_PREFIX) && !TEST_FILE_RE.test(f) && !CONFIG_FILE_RE.test(f),
  )

  if (changedFiles.length === 0) {
    console.log(`[G6] no changed .ts files under ${SCOPE_PREFIX} vs ${baseRef} — nothing to mutate.`)
    return 0
  }

  const changedLines = getChangedLineRanges(cwd, baseRef, changedFiles)
  const mutateArgs = []
  for (const file of changedFiles) {
    const lineSet = changedLines.get(file)
    if (!lineSet || lineSet.size === 0) continue
    for (const r of toRanges(lineSet)) {
      mutateArgs.push(`${file}:${r.start}-${r.end}`)
    }
  }

  if (mutateArgs.length === 0) {
    console.log(`[G6] no mutable lines in the diff vs ${baseRef} — nothing to mutate.`)
    return 0
  }

  console.log(`[G6] mutating ${mutateArgs.length} changed range(s) across ${changedFiles.length} file(s):`)
  for (const a of mutateArgs) console.log(`  - ${a}`)

  fs.rmSync(REPORT_PATH, { force: true })
  const startedAt = Date.now()
  const result = spawnSync(
    'npx',
    ['stryker', 'run', '--mutate', mutateArgs.join(','), '--reporters', 'json,clear-text', '--logLevel', 'info'],
    { cwd, stdio: 'inherit', shell: true }, // Windows: .cmd shims (npx.cmd) fail with EINVAL under shell:false
  )
  const durationMs = Date.now() - startedAt
  console.log(`[G6] Stryker run took ${(durationMs / 1000).toFixed(1)}s.`)

  if (!fs.existsSync(REPORT_PATH)) {
    console.error(`[G6] FAIL — Stryker did not produce ${path.relative(cwd, REPORT_PATH)} (exit code ${result.status}). Treating as a gate failure, not a silent pass.`)
    return 1
  }

  const report = JSON.parse(fs.readFileSync(REPORT_PATH, 'utf-8'))
  const changedFileSet = new Set(changedFiles);
  const survivors = []

  for (const [file, data] of Object.entries(report.files)) {
    const normalizedFile = file.replace(/\\/g, '/')
    if (!changedFileSet.has(normalizedFile)) continue
    const lineSet = changedLines.get(normalizedFile) ?? new Set()
    for (const mutant of data.mutants) {
      if (!NOT_KILLED.has(mutant.status)) continue
      if (!lineSet.has(mutant.location.start.line)) continue
      survivors.push({ file: normalizedFile, ...mutant })
    }
  }

  if (survivors.length > 0) {
    console.error(`\n[G6] FAIL — ${survivors.length} surviving/uncovered mutant(s) in the diff:`)
    for (const s of survivors) {
      console.error(`  - ${s.file}:${s.location.start.line} [${s.status}] ${s.mutatorName} -> \`${s.replacement}\``)
    }
    return 1
  }

  console.log(`\n[G6] PASS — 0 surviving mutants in the diff.`)
  return 0
}

main()
  .then((code) => process.exit(code))
  .catch((err) => {
    console.error('[G6] gate crashed:', err)
    process.exit(1)
  })
