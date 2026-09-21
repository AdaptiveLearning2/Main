/**
 * The sink rules, and nothing else, so CI can gate on them.
 *
 * `npm run lint` is deliberately non-blocking: it runs against a backlog of
 * fourteen pre-existing errors, and making it blocking before those are gone
 * would fail every PR. That is fine for style and wrong for a security
 * guardrail -- a rule nobody can fail is not enforcement, and this one exists
 * precisely so a sink cannot be added quietly.
 *
 * So this config extends nothing. No `js.configs.recommended`, no react-hooks,
 * no react-refresh: the backlog is entirely in those, so it cannot reach here,
 * and `npm run lint:sinks` is red if and only if a sink was added.
 *
 * The rules themselves live in `eslint.sinks.js`, shared with the main config.
 */
import react from 'eslint-plugin-react'
import reactHooks from 'eslint-plugin-react-hooks'
import globals from 'globals'
import { defineConfig, globalIgnores } from 'eslint/config'
import { sinkRules } from './eslint.sinks.js'

export default defineConfig([
  // Same ignores as the main config. `dist` is built output -- minified
  // bundles do contain these patterns, from React itself, and linting them
  // would make this gate fail on whether someone had run a build.
  globalIgnores(['dist', 'coverage']),
  {
    files: ['**/*.{js,jsx}'],
    // This config enables a deliberate subset, so every `eslint-disable` in
    // the tree is for a rule it does not run. Left on, each one reports as an
    // unused directive and the gate is noisy about code it has no opinion on.
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
    // `react-hooks` is registered but none of its rules are enabled. Source
    // files carry `eslint-disable-next-line react-hooks/exhaustive-deps`, and
    // a directive naming a rule no config defines is itself an error -- five
    // of them, in files this gate has nothing to say about.
    plugins: { react, 'react-hooks': reactHooks },
    rules: sinkRules,
  },
])
