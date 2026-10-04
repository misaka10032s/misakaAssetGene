#!/usr/bin/env node
// G3(b) — "newly added/changed test files must not contain zero assertions."
//
// Ported from D:/backup/CSIA/.WEB/misaka_site2.0/frontend/quality-gates/check-test-assertions.mjs
// (verified recipe) with the SAME fix that recipe's own header documents: `languageOptions.parser`
// MUST be set to the TypeScript parser (`tseslint.parser`). Without it, ESLint falls back to the
// default `espree` parser, which cannot parse TS-only syntax — a typed `.test.ts` file then
// produces a single FATAL "Parsing error" message with `ruleId: null`, and since only messages
// with `ruleId === 'vitest/expect-expect'` were being counted, that fatal error was silently
// ignored and a planted zero-assertion `it()` block sailed through as a false PASS on the
// mapleStoryAPI original this recipe fixes. This script counts `msg.fatal` as a violation too
// (belt-and-suspenders — a parse failure means the assertion rule never actually ran).
//
// Scoped to frontend/src/** only, same as G1 — this repo's test files live there once written
// (there are currently 0 .test.ts files, so this gate is a no-op on the untouched tree; it is
// exercised via a temporary canary file for the proof-of-failure pass, see .claude/CLAUDE.md
// `## Code quality gates`).
import { ESLint } from 'eslint'
import path from 'node:path'
import vitestPlugin from '@vitest/eslint-plugin'
import tseslint from 'typescript-eslint'
import { getChangedFiles, getStagedFiles, resolveBaseRef, assertRepoRoot } from './lib/git-diff.mjs'

const cwd = process.cwd()
assertRepoRoot(cwd)
const TEST_FILE_RE = /\.(test|spec)\.[cm]?[jt]sx?$/
const SCOPE_PREFIX = 'frontend/src/'
const EXTENSIONS = ['ts', 'tsx', 'js', 'mjs', 'cjs']
// `--staged` (the commit-time step): only the test files staged for the next commit.
const stagedMode = process.argv.slice(2).includes('--staged')

async function main() {
  const baseRef = stagedMode ? 'the index' : resolveBaseRef(cwd)
  const inScope = (f) => f.startsWith(SCOPE_PREFIX) && TEST_FILE_RE.test(f)
  const stagedTests = getStagedFiles(cwd, EXTENSIONS).filter(inScope)
  const changed = stagedMode ? stagedTests : getChangedFiles(cwd, baseRef, EXTENSIONS).filter(inScope)

  if (changed.length === 0 && stagedTests.length > 0) {
    // The staged list holds test files but the diff listed none: the listing is broken, so nothing was checked.
    console.error(
      `[G3b] FAIL — 0 changed test files vs ${baseRef} while ${stagedTests.length} test file(s) are staged ` +
        `(${stagedTests.join(', ')}): the changed-file listing is wrong, nothing was checked.`,
    )
    return 1
  }
  if (changed.length === 0) {
    console.log(`[G3b] no new/changed test files under ${SCOPE_PREFIX} vs ${baseRef} — nothing to check.`)
    return 0
  }

  const eslint = new ESLint({
    cwd,
    overrideConfigFile: true, // ignore eslint.config.mjs entirely — this is a single-rule pass
    overrideConfig: {
      files: ['**/*.{js,mjs,cjs,ts,tsx}'],
      plugins: { vitest: vitestPlugin },
      rules: { 'vitest/expect-expect': 'error' },
      languageOptions: {
        parser: tseslint.parser,
        parserOptions: { ecmaVersion: 'latest', sourceType: 'module', extraFileExtensions: ['.vue'] },
      },
    },
  })

  const absFiles = changed.map((f) => path.resolve(cwd, f))
  const results = await eslint.lintFiles(absFiles)

  let violations = 0
  for (const result of results) {
    for (const msg of result.messages) {
      if (msg.fatal) {
        violations++
        console.error(`${path.relative(cwd, result.filePath)}:${msg.line} PARSE ERROR (file not checked): ${msg.message}`)
        continue
      }
      if (msg.ruleId === 'vitest/expect-expect') {
        violations++
        console.error(`${path.relative(cwd, result.filePath)}:${msg.line} ${msg.message}`)
      }
    }
  }

  if (violations > 0) {
    console.error(
      `\n[G3b] FAIL — ${violations} test block(s) with zero assertions in ${changed.length} changed test file(s) (base ${baseRef}).`,
    )
    return 1
  }

  console.log(`[G3b] PASS — ${changed.length} changed test file(s), all test blocks assert something (base ${baseRef}).`)
  return 0
}

main()
  .then((code) => process.exit(code))
  .catch((err) => {
    console.error('[G3b] gate crashed:', err)
    process.exit(1)
  })
