/**
 * The sink rules alone, so CI can block on them (`npm run lint` cannot, given its backlog).
 * Extends nothing, so it is red if and only if a sink was added.
 * Rules live in `eslint.sinks.js`.
 */
import react from 'eslint-plugin-react'
import reactHooks from 'eslint-plugin-react-hooks'
import globals from 'globals'
import { defineConfig, globalIgnores } from 'eslint/config'
import { sinkRules } from './eslint.sinks.js'

export default defineConfig([
  // `dist` bundles contain these patterns from React itself.
  globalIgnores(['dist', 'coverage']),
  {
    files: ['**/*.{js,jsx}'],
    // Every `eslint-disable` in the tree is for a rule this config does not run.
    linterOptions: { reportUnusedDisableDirectives: 'off' },
    languageOptions: {
      ecmaVersion: 'latest',
      globals: globals.browser,
      parserOptions: {
        ecmaVersion: 'latest',
        ecmaFeatures: { jsx: true },
        sourceType: 'module',
      },
    },
    // `react-hooks` registered, no rules enabled: an `eslint-disable` naming an
    // undefined rule is itself an error.
    plugins: { react, 'react-hooks': reactHooks },
    rules: sinkRules,
  },
])
