#!/usr/bin/env node
// G1 — "0 new ESLint warnings/errors on changed lines."
//
// This repo's eslint.config.mjs (repo root) is clean of hard errors but carries a real
// pre-existing backlog — 880 warnings measured 2026-08-27 across frontend/src/**, almost all
// Vue formatting rules (vue/singleline-html-element-content-newline,
// vue/max-attributes-per-line, vue/html-*) plus a handful of unused-vars/multi-word-component.
// A repo-wide `eslint --max-warnings=0` would fail on day one for every contributor regardless
// of what they touched, so this gate uses the same LINE-range diff-scoping model
// misaka_site2.0's check-lint-diff.mjs pioneered (D:/backup/CSIA/.WEB/misaka_site2.0/frontend/
// quality-gates/check-lint-diff.mjs — verified recipe, ported here unchanged in logic): lint
// changed files, but only fail on messages whose line is actually inside the diff's changed
// lines. A brand-new file (absent at baseRef) has no "pre-existing" lines by definition, so
// every line in it counts.
//
// Scope note: `getChangedFiles` is filtered to `frontend/src/**` explicitly (not just by
// extension) — this repo's eslint.config.mjs is frontend-only, and this hard filter is what
// keeps a stray gitignored scratch .ts anywhere else in the tree (or this very quality-gates/
// dir's own .mjs scripts) from ever being fed to ESLint by this gate.
import { ESLint } from 'eslint'
import path from 'node:path'
import { execFileSync } from 'node:child_process'
import { getChangedFiles, getChangedLineRanges, resolveBaseRef, repoPrefix, assertRepoRoot } from './lib/git-diff.mjs'

const cwd = process.cwd()
assertRepoRoot(cwd)
const SCOPE_PREFIX = 'frontend/src/'

function fileExistsAtRef(file, baseRef, prefix) {
  try {
    execFileSync('git', ['cat-file', '-e', `${baseRef}:${prefix}${file}`], { cwd, stdio: 'ignore' })
    return true
  } catch {
    return false
  }
}

async function main() {
  const baseRef = resolveBaseRef(cwd)
  const changed = getChangedFiles(cwd, baseRef, ['ts', 'tsx', 'vue', 'js', 'mjs', 'cjs']).filter((f) =>
    f.startsWith(SCOPE_PREFIX),
  )

  if (changed.length === 0) {
    console.log(`[G1] no new/changed lintable files under ${SCOPE_PREFIX} vs ${baseRef} — nothing to check.`)
    return 0
  }

  const prefix = repoPrefix(cwd)
  const changedLines = getChangedLineRanges(cwd, baseRef, changed)
  const eslint = new ESLint({ cwd }) // uses this repo's own eslint.config.mjs (repo root)
  const absFiles = changed.map((f) => path.resolve(cwd, f))
  const results = await eslint.lintFiles(absFiles)

  let errorCount = 0
  let warningCount = 0
  for (const result of results) {
    const relFile = path.relative(cwd, result.filePath).replace(/\\/g, '/')
    const isNewFile = !fileExistsAtRef(relFile, baseRef, prefix)
    const lineSet = changedLines.get(relFile) ?? new Set()

    for (const msg of result.messages) {
      if (!isNewFile && !lineSet.has(msg.line)) continue // pre-existing, unrelated to this diff
      const sev = msg.severity === 2 ? 'error' : 'warning'
      if (msg.severity === 2) errorCount++
      else warningCount++
      console.error(`${relFile}:${msg.line}:${msg.column} ${sev} ${msg.message} (${msg.ruleId ?? 'n/a'})`)
    }
  }

  const total = errorCount + warningCount
  if (total > 0) {
    console.error(`\n[G1] FAIL — ${errorCount} error(s), ${warningCount} warning(s) on changed lines across ${changed.length} changed file(s) (base ${baseRef}).`)
    return 1
  }

  console.log(`[G1] PASS — ${changed.length} changed file(s), 0 errors/warnings on changed lines (base ${baseRef}).`)
  return 0
}

main()
  .then((code) => process.exit(code))
  .catch((err) => {
    console.error('[G1] gate crashed:', err)
    process.exit(1)
  })
