// Shared diff-scoping helper for the diff-based gates (G1 lint-diff, G3 assertion-presence, G4
// new-cycle check, G5 diff coverage, G6 diff mutation). Every gate that needs "only what
// changed" uses this so the definition of "changed" stays identical across all of them.
//
// Ported from D:/backup/CSIA/.WEB/misaka_site2.0/frontend/quality-gates/lib/git-diff.mjs
// (verified recipe), unchanged in logic. Scope model: the base ref is the merge-base with
// `main` (this repo's main branch), and "changed" means "working tree right now vs that merge
// base" (git diff <base>, no upper bound). Override with QUALITY_BASE_REF for a
// narrower/explicit comparison.
import { execFileSync } from 'node:child_process'
import fs from 'node:fs'
import path from 'node:path'

function git(args, cwd) {
  return execFileSync('git', args, { cwd, encoding: 'utf-8', maxBuffer: 64 * 1024 * 1024 })
}

// DX guard (fresh-reviewer non-blocking finding, 2026-08-27): every gate script assumes
// process.cwd() IS the repo root — true for the documented `npm run gate:X` invocation (npm
// always cd's to the directory containing the package.json it found), but a bare
// `node quality-gates/frontend/check-X.mjs` run from inside frontend/ silently computes every
// path wrong instead, and the resulting crash (e.g. madge fed a doubled-up tsconfig path) is a
// cryptic stack trace with no hint that the invocation itself was the problem. This check runs
// first in every gate script and fails with a clear, actionable message instead.
export function assertRepoRoot(cwd) {
  const marker = path.join(cwd, 'package.json')
  if (!fs.existsSync(marker)) {
    console.error(
      `[quality-gates] "${cwd}" has no package.json — this script must be run from the repo ` +
        `root via "npm run gate:<name>" (or "npm run --prefix <repo-root> gate:<name>" from ` +
        `elsewhere), never as a bare "node quality-gates/frontend/<script>.mjs" from inside ` +
        `frontend/ or any other subdirectory.`,
    )
    process.exit(1)
  }
}

/** Resolve the base ref to diff against. */
export function resolveBaseRef(cwd) {
  if (process.env.QUALITY_BASE_REF && process.env.QUALITY_BASE_REF.trim()) {
    return process.env.QUALITY_BASE_REF.trim()
  }
  try {
    const base = git(['merge-base', 'HEAD', 'main'], cwd).trim()
    if (base) return base
  } catch {
    // `main` not reachable (e.g. detached/shallow) — fall through.
  }
  try {
    return git(['rev-parse', 'HEAD~1'], cwd).trim()
  } catch {
    return git(['rev-parse', 'HEAD'], cwd).trim() // single-commit repo: yields an empty diff
  }
}

/** git's own prefix for `cwd` relative to the repo top-level, e.g. "frontend/". */
export function repoPrefix(cwd) {
  return git(['rev-parse', '--show-prefix'], cwd).trim()
}

function stripPrefix(p, prefix) {
  return prefix && p.startsWith(prefix) ? p.slice(prefix.length) : p
}

/**
 * List changed files (added/copied/modified/renamed — never deleted) under `cwd`, filtered to
 * the given extensions, returned as paths relative to `cwd` (not the repo top-level).
 */
export function getChangedFiles(cwd, baseRef, extensions) {
  const prefix = repoPrefix(cwd)
  const patterns = extensions.map((e) => `*.${e}`)
  let out
  try {
    out = git(['diff', '--name-only', '--diff-filter=ACMR', baseRef, '--', ...patterns], cwd)
  } catch (err) {
    throw new Error(`git diff failed against base ref "${baseRef}": ${err.message}`)
  }
  return out
    .split('\n')
    .map((s) => s.trim())
    .filter(Boolean)
    .map((p) => p.replace(/\\/g, '/'))
    .map((p) => stripPrefix(p, prefix))
}

/**
 * Map<relPath, Set<lineNumber>> of lines added/changed on the "new" side for the given files.
 * Pure deletions contribute no lines (nothing new to require coverage/mutation for).
 */
export function getChangedLineRanges(cwd, baseRef, files) {
  const result = new Map()
  if (files.length === 0) return result
  const prefix = repoPrefix(cwd)
  const diffOut = git(['diff', '--unified=0', '--diff-filter=ACMR', baseRef, '--', ...files], cwd)
  let currentFile = null
  for (const line of diffOut.split('\n')) {
    if (line.startsWith('+++ ')) {
      const raw = line.slice(4).trim()
      if (raw === '/dev/null') {
        currentFile = null
        continue
      }
      const cleaned = stripPrefix(raw.replace(/^b\//, '').replace(/\\/g, '/'), prefix)
      currentFile = cleaned
      if (!result.has(currentFile)) result.set(currentFile, new Set())
      continue
    }
    if (line.startsWith('@@ ') && currentFile) {
      const m = /@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@/.exec(line)
      if (m) {
        const startLine = parseInt(m[1], 10)
        const count = m[2] === undefined ? 1 : parseInt(m[2], 10)
        for (let i = 0; i < count; i++) result.get(currentFile).add(startLine + i)
      }
    }
  }
  return result
}
