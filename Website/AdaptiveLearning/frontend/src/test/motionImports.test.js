/** `motion` brings every animation feature back into the entry chunk; `m` takes them from <LazyMotion>. */
import { it, expect, beforeAll } from 'vitest'
import { ESLint } from 'eslint'
import path from 'node:path'
import url from 'node:url'

const ROOT = path.resolve(path.dirname(url.fileURLToPath(import.meta.url)), '../..')

let eslint
beforeAll(() => {
  // Parsed, not grepped: a comment or a string naming `motion` is not an import.
  eslint = new ESLint({
    cwd: ROOT,
    overrideConfigFile: true,
    overrideConfig: {
      files: ['**/*.{js,jsx}'],
      languageOptions: { ecmaVersion: 'latest', sourceType: 'module',
                         parserOptions: { ecmaFeatures: { jsx: true } } },
      rules: { 'no-restricted-imports': ['error', { paths: [{
        name: 'framer-motion', importNames: ['motion'],
        message: 'Use `m`; <LazyMotion> in App.jsx supplies its features.' }] }] },
    },
  })
})

it.each([
  ['a named import', "import { motion } from 'framer-motion'"],
  ['a renamed import', "import { motion as Motion } from 'framer-motion'"],
  ['a namespace import', "import * as fm from 'framer-motion'"],
])('flags %s, so the scan below can fail', async (_name, code) => {
  const [result] = await eslint.lintText(`${code}\n`, { filePath: path.join(ROOT, 'src/__probe.jsx') })
  expect(result.messages.map(m => m.ruleId)).toEqual(['no-restricted-imports'])
}, 60_000)

it('no source file imports motion from framer-motion', async () => {
  const results = await eslint.lintFiles(['src/**/*.{js,jsx}'])
  // Not every message: disable comments name rules this one-rule config never loads.
  const found = results.flatMap(r => r.messages
    .filter(m => m.ruleId === 'no-restricted-imports' || m.fatal)
    .map(m => `${path.relative(ROOT, r.filePath)}:${m.line} ${m.message}`))
  expect(results.length).toBeGreaterThan(100)
  expect(found).toEqual([])
}, 60_000)
