import js from '@eslint/js'
import globals from 'globals'
import react from 'eslint-plugin-react'
import reactHooks from 'eslint-plugin-react-hooks'
import reactRefresh from 'eslint-plugin-react-refresh'
import { defineConfig, globalIgnores } from 'eslint/config'
import { sinkRules } from './eslint.sinks.js'
import { withMotionRules } from './eslint.motion.js'

export default defineConfig([
  // `coverage/` is generated; linted, the result would depend on whether coverage had ever been run.
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
      // A component the compiler cannot build is unseen by every compiler-backed hooks rule.
      'react-hooks/todo': 'error',
      // A context's hook lives beside its provider.
      'react-refresh/only-export-components': ['error', {
        allowConstantExport: true,
        allowExportNames: ['useAuth', 'useTheme'],
      }],
      // Editor feedback; CI gates on `eslint.sinks.config.js`.
      ...withMotionRules(sinkRules),
    },
  },
  {
    // No `eslint-disable` in app source: a react-hooks one blinds every compiler-backed rule to
    // the whole component. Tests keep theirs (the motion probes need one).
    files: ['src/**/*.{js,jsx}'],
    ignores: ['src/**/*.{test,spec}.{js,jsx}', 'src/test/**'],
    linterOptions: { noInlineConfig: true },
  },
  {
    // vitest `globals: true` injects describe/it/expect/vi, and runs in Node (`process`, `global`).
    files: ['**/*.{test,spec}.{js,jsx}', 'src/test/**/*.{js,jsx}'],
    languageOptions: {
      globals: { ...globals.browser, ...globals.vitest, ...globals.node },
    },
  },
])
