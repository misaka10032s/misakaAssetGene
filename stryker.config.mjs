// @ts-check
// G6 (diff mutation) base config — ported from
// D:/backup/CSIA/.WEB/misaka_site2.0/frontend/stryker.config.mjs (verified recipe). The
// `mutate` list here is a fallback only — the real gate (quality-gates/frontend/mutation-diff.mjs)
// always passes an explicit --mutate list scoped to the current diff's changed lines, so
// mutating the whole `frontend/src/` tree here never actually runs in normal use.
//
// .vue files are deliberately NOT in `mutate`: Stryker has no maintained Vue-SFC mutator for
// this stack, so G6 only covers plain .ts logic modules — a real, reported scope gap, not an
// oversight (same gap misaka_site2.0 documents). vitest.config.ts's own `test.exclude` already
// keeps tmp/ and other scratch dirs out of the test run Stryker drives.
/** @type {import('@stryker-mutator/api/core').PartialStrykerOptions} */
const config = {
  packageManager: 'npm',
  testRunner: 'vitest',
  reporters: ['json', 'clear-text'],
  coverageAnalysis: 'perTest',
  ignoreStatic: true,
  tempDirName: '.stryker-tmp',
  cleanTempDir: 'always',
  mutate: [
    'frontend/src/**/*.ts',
    '!frontend/src/**/*.test.ts',
    '!frontend/src/**/*.spec.ts',
  ],
}

export default config
