import js from '@eslint/js'
import globals from 'globals'
import react from 'eslint-plugin-react'
import reactHooks from 'eslint-plugin-react-hooks'
import reactRefresh from 'eslint-plugin-react-refresh'
import { defineConfig, globalIgnores } from 'eslint/config'

export default defineConfig([
  globalIgnores(['dist']),
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
      // `ignoreRestSiblings` covers destructuring-to-omit — `const { x, ...rest }
      // = obj` to build an object *without* `x`, which is how the tests construct
      // a payload that predates a field. The binding is unused by design there,
      // and deleting it to satisfy the rule would put the key back.
      'no-unused-vars': ['error', {
        varsIgnorePattern: '^[A-Z_]',
        ignoreRestSiblings: true,
      }],
      // `no-unused-vars` cannot see JSX. Without this, every identifier used
      // only inside JSX — `motion` from framer-motion, an `icon: Icon` prop
      // rendered as `<Icon />` — reads as an unused import. That was 40 of the
      // 65 errors in the backlog, all false. Only this one rule is enabled;
      // eslint-plugin-react's recommended config brings a large ruleset that
      // would add to the backlog rather than clear it.
      'react/jsx-uses-vars': 'error',
    },
  },
  {
    // Test files run under vitest with `globals: true` (see vite.config.js),
    // so describe/it/expect/vi are injected rather than imported. Without this
    // every assertion reads as a no-undef error.
    files: ['**/*.{test,spec}.{js,jsx}', 'src/test/**/*.{js,jsx}'],
    languageOptions: {
      globals: { ...globals.browser, ...globals.vitest },
    },
  },
])
