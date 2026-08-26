// @ts-check
// ESLint flat config — Vue 3 + TypeScript (Vite project). Lives at repo ROOT (not
// frontend/eslint.config.mjs) because package.json and every other build config in this repo
// (uno.config.ts) already sit at root; frontend/ has no package.json of its own. Scoped
// explicitly to frontend/src/**, so nothing outside the frontend tree (core/, tools/,
// workers/, scripts/) is ever linted by this config.
import pluginVue from 'eslint-plugin-vue'
import tseslint from 'typescript-eslint'

export default tseslint.config(
  // --- ignores ---
  {
    ignores: [
      'dist/**',
      'node_modules/**',
      'frontend/node_modules/**',
      'coverage/**',
      '.stryker-tmp/**',
      'src-tauri/**',
      // non-frontend trees — this config is frontend-only
      'core/**',
      'tools/**',
      'workers/**',
      'tests/**',
      'scripts/**',
      'quality-gates/**',
      '.claude/worktree/**',
      '**/*.d.ts',
    ],
  },

  // --- TypeScript base ---
  ...tseslint.configs.recommended,

  // --- Vue 3 recommended (includes vue-essential + vue-strongly-recommended + vue-recommended) ---
  ...pluginVue.configs['flat/recommended'],

  // --- Project scope + overrides ---
  {
    files: ['frontend/src/**/*.ts', 'frontend/src/**/*.vue'],
    languageOptions: {
      parserOptions: {
        // vue files: vue parser handles <template>; ts parser handles <script>
        parser: tseslint.parser,
        extraFileExtensions: ['.vue'],
      },
    },
    rules: {
      '@typescript-eslint/no-unused-vars': ['warn', { argsIgnorePattern: '^_', varsIgnorePattern: '^_' }],
      'vue/multi-word-component-names': 'warn',
    },
  },
)
