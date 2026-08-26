// Minimal lcov.info parser — only the DA: (line hit count) records, which is all diff
// coverage needs. Ported from
// D:/backup/CSIA/.WEB/misaka_site2.0/frontend/quality-gates/lib/lcov.mjs (verified recipe),
// unchanged. Producer here is @vitest/coverage-v8 (test.coverage.reporter includes 'lcov' —
// see quality-gates/frontend/../../vitest.config.ts).
import fs from 'node:fs'

/** Returns Map<relPath (forward-slash), Map<lineNumber, hitCount>>. */
export function parseLcov(lcovPath) {
  const text = fs.readFileSync(lcovPath, 'utf-8')
  const files = new Map()
  let current = null
  for (const line of text.split('\n')) {
    if (line.startsWith('SF:')) {
      const p = line.slice(3).trim().replace(/\\/g, '/')
      current = new Map()
      files.set(p, current)
    } else if (line.startsWith('DA:') && current) {
      const [lineNoStr, hitsStr] = line.slice(3).split(',')
      current.set(parseInt(lineNoStr, 10), parseInt(hitsStr, 10))
    } else if (line.startsWith('end_of_record')) {
      current = null
    }
  }
  return files
}
