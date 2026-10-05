/**
 * The sink and motion rules alone, as their own CI step: no change to the main config can switch them off.
 * Extends nothing, so it is red if and only if a sink or a motion misuse was added.
 * Rules live in `eslint.sinks.js` and `eslint.motion.js`.
 */
import react from 'eslint-plugin-react'
import reactHooks from 'eslint-plugin-react-hooks'
import globals from 'globals'
import { defineConfig, globalIgnores } from 'eslint/config'
import { sinkRules } from './eslint.sinks.js'
import { withMotionRules } from './eslint.motion.js'

export default defineConfig([
  // `dist` bundles contain these patterns from React itself.
  globalIgnores(['dist', 'coverage']),
  {
    files: ['**/*.{js,jsx}'],
    languageOptions: {
      ecmaVersion: 'latest',
      globals: globals.browser,
      parserOptions: {
        ecmaVersion: 'latest',
        ecmaFeatures: { jsx: true },
        sourceType: 'module',
      },
    },
    // `react-hooks` registered, no rules enabled: tests may still carry a react-hooks
    // disable, and one naming an undefined rule is itself an error.
    plugins: { react, 'react-hooks': reactHooks },
    rules: withMotionRules(sinkRules),
  },
])
