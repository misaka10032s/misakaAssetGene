#!/usr/bin/env node
// G4 — "no NEW import cycle relative to a version-controlled baseline."
//
// Ported from D:/backup/CSIA/.WEB/misaka_site2.0/frontend/quality-gates/check-import-cycles.mjs
// (verified recipe) — same tool (madge) and the same `tsConfig` fix that recipe documents:
// without an explicit tsConfig, madge's detective falls back to a flow+jsx Babel parser and
// chokes on plain TS syntax (generics, `<script setup lang="ts">`). `tsConfig:
// 'frontend/tsconfig.json'` fixes it and resolves this repo's `@/*` path alias the same way
// vue-tsc does.
//
// This does NOT clean up pre-existing cycles — it only blocks NEW ones.
import madge from 'madge'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { assertRepoRoot } from './lib/git-diff.mjs'

const cwd = process.cwd()
assertRepoRoot(cwd)
const __dirname = path.dirname(fileURLToPath(import.meta.url))
const BASELINE_PATH = path.join(__dirname, 'import-cycle-baseline.json')

function cycleKey(cycle) {
  return [...cycle].sort().join('|')
}

async function findCycles() {
  const res = await madge('frontend/src', {
    baseDir: cwd,
    fileExtensions: ['ts', 'vue'],
    tsConfig: path.join(cwd, 'frontend', 'tsconfig.json'),
  })
  return res.circular()
}

function loadBaseline() {
  if (!fs.existsSync(BASELINE_PATH)) return []
  return JSON.parse(fs.readFileSync(BASELINE_PATH, 'utf-8'))
}

async function main() {
  const updateMode = process.argv.includes('--update-baseline')
  const current = await findCycles()

  if (updateMode) {
    fs.writeFileSync(BASELINE_PATH, JSON.stringify(current, null, 2) + '\n')
    console.log(`[G4] baseline updated — ${current.length} cycle(s) recorded at ${path.relative(cwd, BASELINE_PATH)}.`)
    return 0
  }

  const baseline = loadBaseline()
  const baselineKeys = new Set(baseline.map(cycleKey))
  const currentKeys = new Set(current.map(cycleKey))

  const newCycles = current.filter((c) => !baselineKeys.has(cycleKey(c)))
  const resolvedCycles = baseline.filter((c) => !currentKeys.has(cycleKey(c)))

  if (resolvedCycles.length > 0) {
    console.log(`[G4] note: ${resolvedCycles.length} baseline cycle(s) no longer exist — consider re-running with --update-baseline to shrink the baseline:`)
    for (const c of resolvedCycles) console.log(`  - ${c.join(' -> ')}`)
  }

  if (newCycles.length > 0) {
    console.error(`[G4] FAIL — ${newCycles.length} NEW import cycle(s) not present in the baseline:`)
    for (const c of newCycles) console.error(`  - ${c.join(' -> ')} -> ${c[0]}`)
    console.error(`\nBaseline: ${path.relative(cwd, BASELINE_PATH)} (${baseline.length} pre-existing cycle(s), unaffected).`)
    return 1
  }

  console.log(`[G4] PASS — ${current.length} total cycle(s), 0 new vs baseline (${baseline.length} pre-existing).`)
  return 0
}

main()
  .then((code) => process.exit(code))
  .catch((err) => {
    console.error('[G4] gate crashed:', err)
    process.exit(1)
  })
