// Separate from frontend/vite.config.ts (app build config) — vitest needs its own explicit
// `test.include`/`test.exclude` so gate:g3/g5/g6 (this file backs all three) can never pick up
// a gitignored scratch script or generated artifact by relying on a tool default scan (see
// .claude/CLAUDE.md `## Code quality gates` — the mapleStoryAPI vitest incident this scoping
// mirrors: an unscoped vitest config picked up a stale scratch test under frontend/tmp/ and
// blocked every frontend commit through the hook-enforced gate).
//
// `root` is deliberately left at the repo root (NOT frontend/vite.config.ts's own
// `root: resolve(frontendRoot)`) so vitest's lcov `SF:` paths and `include`/`exclude` globs
// stay in the SAME "relative to repo root" coordinate space that `git diff` (and therefore
// quality-gates/frontend/diff-coverage.mjs + mutation-diff.mjs) already use — found empirically
// during the G5 proof-of-failure pass: with `test.root` set to frontend/, lcov wrote
// `SF:src\utils\foo.ts` while the diff-scoped gate looked up `frontend/src/utils/foo.ts`, a
// silent path-prefix mismatch that made every diff-coverage lookup miss and report a false 0%.
import { defineConfig } from 'vitest/config'
import vue from '@vitejs/plugin-vue'
import { resolve } from 'node:path'

const repoRoot = import.meta.dirname

export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: {
      '@': resolve(repoRoot, 'frontend/src'),
    },
  },
  test: {
    environment: 'jsdom',
    include: ['frontend/src/**/*.{test,spec}.{ts,tsx}'],
    exclude: [
      '**/node_modules/**',
      '**/dist/**',
      '**/tmp/**',
      '**/temp/**',
      '**/.cache/**',
      '**/coverage/**',
      '**/.stryker-tmp/**',
    ],
    // 0 test files today (measured 2026-08-27) — a bare `vitest run` fails loud with no test
    // files found by default; passWithNoTests keeps gate:g3's "tests green" leg green on the
    // untouched tree instead of that being a false-red install defect. The FIRST real test
    // file added flips this back to a normal pass/fail run automatically.
    passWithNoTests: true,
    coverage: {
      provider: 'v8',
      reporter: ['text', 'lcov'],
      reportsDirectory: resolve(repoRoot, 'coverage'),
      include: ['frontend/src/**/*.{ts,vue}'],
      exclude: ['frontend/src/**/*.{test,spec}.{ts,tsx}', '**/*.d.ts', '**/*.config.*'],
    },
  },
})
