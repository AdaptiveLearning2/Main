import js from '@eslint/js'
import globals from 'globals'
import react from 'eslint-plugin-react'
import reactHooks from 'eslint-plugin-react-hooks'
import reactRefresh from 'eslint-plugin-react-refresh'
import { defineConfig, globalIgnores } from 'eslint/config'
import { sinkRules } from './eslint.sinks.js'

export default defineConfig([
  // `coverage/` is generated; linting it skews the backlog count.
  globalIgnores(['dist', 'coverage']),
  {
    files: ['**/*.{js,jsx}'],
    extends: [
      js.configs.recommended,
      reactHooks.configs.flat.recommended,
      reactRefresh.configs.vite,
    ],
    languageOptions: {
      ecmaVersion: 2020,
      globals: globals.browser,
      parserOptions: {
        ecmaVersion: 'latest',
        ecmaFeatures: { jsx: true },
        sourceType: 'module',
      },
    },
    plugins: { react },
    rules: {
      // `ignoreRestSiblings`: `const { x, ...rest } = obj` omits `x` by design.
      'no-unused-vars': ['error', {
        varsIgnorePattern: '^[A-Z_]',
        ignoreRestSiblings: true,
      }],
      // `no-unused-vars` cannot see JSX. Only this rule, not the plugin's recommended set.
      'react/jsx-uses-vars': 'error',
      // Editor feedback; CI gates on `eslint.sinks.config.js`.
      ...sinkRules,
    },
  },
  {
    // vitest `globals: true` injects describe/it/expect/vi.
    files: ['**/*.{test,spec}.{js,jsx}', 'src/test/**/*.{js,jsx}'],
    languageOptions: {
      globals: { ...globals.browser, ...globals.vitest },
    },
  },
])
