/** What the compiler behind the hooks rules cannot build, linted through the real `eslint.config.js`. */
import { describe, it, expect, beforeAll } from 'vitest'
import { ESLint } from 'eslint'
import path from 'node:path'
import url from 'node:url'

const ROOT = path.resolve(path.dirname(url.fileURLToPath(import.meta.url)), '../..')

let eslint
// The warm-up lint loads config and plugins under its own timeout, outside the first test's 5 s budget.
beforeAll(async () => {
  eslint = new ESLint({ cwd: ROOT, overrideConfigFile: path.join(ROOT, 'eslint.config.js') })
  await rules('const go = () => save()')
}, 60_000)

/** The rule ids reported for a component whose body holds `code`, linted as a file under `src/`. */
async function rules(code) {
  const source = [
    "import { useRef, useState } from 'react'",
    'export function Probe({ save }) {',
    '  const [busy, setBusy] = useState(false)',
    '  const ref = useRef(null)',
    code,
    "  return <button onClick={go} data-ref={ref}>{busy ? 'saving' : 'save'}</button>",
    '}',
  ].join('\n')
  const [result] = await eslint.lintText(source, { filePath: path.join(ROOT, 'src/__probe.jsx') })
  return result.messages.map(m => m.ruleId)
}

// Each makes the compiler skip the whole component, and with it every compiler-backed rule.
const UNBUILDABLE = [
  ['a finally clause', 'const go = async () => { setBusy(true); try { await save() } finally { setBusy(false) } }'],
  ['a value block in a try', 'const go = async () => { try { await save(busy || null) } catch (e) { console.error(e) } }'],
  ['a throw in a try', "const go = async () => { try { if (!(await save())) throw new Error('no') } catch (e) { console.error(e) } }"],
  ['a logical assignment', 'const go = () => { ref.current ??= save() }'],
  ['an inline arrow as a default', 'const go = (v, f = x => x) => f(v)'],
]

// What is built: the chain this codebase uses for cleanup that must run, and a try of plain statements.
const BUILDABLE = [
  ['a promise chain', 'const go = () => { setBusy(true); return save().catch(e => console.error(e)).finally(() => setBusy(false)) }'],
  ['a try of plain statements', 'const go = async () => { try { await save() } catch (e) { console.error(e) } }'],
]

describe('a component the compiler cannot build is reported, not skipped silently', () => {
  it.each(UNBUILDABLE)('%s', async (_name, code) => {
    expect(await rules(code)).toContain('react-hooks/todo')
  })
})

describe('the shapes that replace them are built', () => {
  it.each(BUILDABLE)('%s', async (_name, code) => {
    expect(await rules(code)).not.toContain('react-hooks/todo')
  })
})
