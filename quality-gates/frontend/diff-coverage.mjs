#!/usr/bin/env node
// G5 — diff coverage >= threshold (60% default, changed lines only).
//
// Ported from D:/backup/CSIA/.WEB/misaka_site2.0/frontend/quality-gates/diff-coverage.mjs
// (verified recipe) — same deviation from the Python-family diff-cover tool for the same
// reason: a pure Node/TS repo has no other Python toolchain in play, so pulling in diff-cover
// just to read an lcov file is a foreign-toolchain tax with no offsetting benefit.
//
// Must run AFTER `vitest run --coverage` (see quality-gates/frontend's package.json wiring) —
// reads coverage/lcov.info, does not generate it. Scoped to frontend/src/** only.
import fs from 'node:fs'
import path from 'node:path'
import { parseLcov } from './lib/lcov.mjs'
import { getChangedFiles, getChangedLineRanges, resolveBaseRef, assertRepoRoot } from './lib/git-diff.mjs'

const cwd = process.cwd()
assertRepoRoot(cwd)
const TEST_FILE_RE = /\.(test|spec)\.[cm]?[jt]sx?$/
const CONFIG_FILE_RE = /\.config\.[cm]?[jt]s$/
const SCOPE_PREFIX = 'frontend/src/'
const THRESHOLD = Number(process.env.QUALITY_DIFF_COVERAGE_THRESHOLD ?? 60)
const LCOV_PATH = path.join(cwd, 'coverage', 'lcov.info')

async function main() {
  const baseRef = resolveBaseRef(cwd)
  const changedFiles = getChangedFiles(cwd, baseRef, ['ts', 'vue']).filter(
    (f) => f.startsWith(SCOPE_PREFIX) && !TEST_FILE_RE.test(f) && !CONFIG_FILE_RE.test(f) && !f.endsWith('.d.ts'),
  )

  if (changedFiles.length === 0) {
    console.log(`[G5] no coverable changed files under ${SCOPE_PREFIX} vs ${baseRef} — nothing to check.`)
    return 0
  }

  if (!fs.existsSync(LCOV_PATH)) {
    console.error(
      `[G5] FAIL — ${LCOV_PATH} not found. Run "vitest run --coverage" first (gate:g5 does this for you).`,
    )
    return 1
  }

  const lcov = parseLcov(LCOV_PATH)
  const changedLines = getChangedLineRanges(cwd, baseRef, changedFiles)

  let totalExecutable = 0
  let totalCovered = 0
  const perFile = []

  for (const file of changedFiles) {
    const lines = changedLines.get(file)
    if (!lines || lines.size === 0) continue // e.g. pure deletion, or file rename with no content diff

    const fileCoverage = lcov.get(file)
    const uncovered = []
    let fileExecutable = 0
    let fileCovered = 0

    if (!fileCoverage) {
      for (const ln of lines) uncovered.push(ln)
      fileExecutable = lines.size
    } else {
      for (const ln of lines) {
        const hits = fileCoverage.get(ln)
        if (hits === undefined) continue // non-executable line (blank/comment/type-only) — not counted
        fileExecutable++
        if (hits > 0) fileCovered++
        else uncovered.push(ln)
      }
    }

    totalExecutable += fileExecutable
    totalCovered += fileCovered
    if (fileExecutable > 0) {
      perFile.push({ file, executable: fileExecutable, covered: fileCovered, uncovered })
    }
  }

  const pct = totalExecutable === 0 ? 100 : (totalCovered / totalExecutable) * 100

  console.log(`[G5] diff coverage vs ${baseRef}: ${pct.toFixed(1)}% (${totalCovered}/${totalExecutable} changed executable lines), threshold ${THRESHOLD}%`)
  for (const f of perFile) {
    const filePct = ((f.covered / f.executable) * 100).toFixed(1)
    console.log(`  - ${f.file}: ${filePct}% (${f.covered}/${f.executable})${f.uncovered.length ? ` — uncovered lines: ${f.uncovered.join(',')}` : ''}`)
  }

  if (pct < THRESHOLD) {
    console.error(`\n[G5] FAIL — ${pct.toFixed(1)}% < ${THRESHOLD}% threshold.`)
    return 1
  }
  console.log(`\n[G5] PASS.`)
  return 0
}

main()
  .then((code) => process.exit(code))
  .catch((err) => {
    console.error('[G5] gate crashed:', err)
    process.exit(1)
  })
