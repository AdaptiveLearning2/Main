/** Every spelling of every XSS sink, linted through the real `eslint.sinks.config.js` and read from the report. */
import { describe, it, expect, beforeAll } from 'vitest'
import { ESLint } from 'eslint'
import path from 'node:path'
import url from 'node:url'

const ROOT = path.resolve(path.dirname(url.fileURLToPath(import.meta.url)), '../..')

let eslint
// The warm-up lint loads config and plugins under its own timeout, outside the first test's 5 s budget.
beforeAll(async () => {
  eslint = new ESLint({
    cwd: ROOT,
    overrideConfigFile: path.join(ROOT, 'eslint.sinks.config.js'),
  })
  await lint('const warm = 1')
}, 60_000)

/** The rule ids reported for one snippet, linted as a file under `src/`. */
async function lint(code) {
  const [result] = await eslint.lintText(`export function probe(el, s, props, userText) {\n${code}\n}\n`,
                                         { filePath: path.join(ROOT, 'src/__probe.jsx') })
  return result.messages
}

// Both spellings of each sink: Identifier `.name` and quoted Literal `.value`.
const SINKS = [
  ['eval, called bare',            'eval(s)'],
  ['eval through window',          'window.eval(s)'],
  ['eval through a quoted key',    "window['eval'](s)"],
  ['eval through globalThis',      "globalThis['eval'](s)"],
  ['new Function',                 'new Function(s)'],
  ['Function called plain',        'Function(s)'],
  ['Function through window',      'window.Function(s)'],
  ['Function quoted',              "window['Function'](s)"],
  ['innerHTML assigned',           'el.innerHTML = s'],
  ['innerHTML assigned, quoted',   "el['innerHTML'] = s"],
  ['outerHTML assigned',           'el.outerHTML = s'],
  ['outerHTML assigned, quoted',   "el['outerHTML'] = s"],
  ['innerHTML as an object key',   'Object.assign(el, { innerHTML: s })'],
  ['innerHTML quoted key',         "Object.assign(el, { 'innerHTML': s })"],
  ['insertAdjacentHTML',           "el.insertAdjacentHTML('beforeend', s)"],
  ['insertAdjacentHTML quoted',    "el['insertAdjacentHTML']('beforeend', s)"],
  ['document.write',               'document.write(s)'],
  ['document.write quoted',        "document['write'](s)"],
  ['document.writeln',             'document.writeln(s)'],
  ['write through an alias',       'const d = document; d.write(s)'],
  ['dSIH as a JSX attribute',      'return <div dangerouslySetInnerHTML={{ __html: userText }} />'],
  ['dSIH as an object key',        'const p = { dangerouslySetInnerHTML: { __html: s } }; return <div {...p} />'],
  ['dSIH as a quoted key',         "const p = { 'dangerouslySetInnerHTML': { __html: s } }; return <div {...p} />"],
  ['dSIH assigned',                'props.dangerouslySetInnerHTML = { __html: s }'],
  ['dSIH assigned, quoted',        "props['dangerouslySetInnerHTML'] = { __html: s }"],
]

describe('the sink gate flags', () => {
  it.each(SINKS)('%s', async (_name, code) => {
    const messages = await lint(code)
    expect(messages.map(m => m.ruleId)).toEqual(
      expect.arrayContaining([expect.stringMatching(/no-restricted-syntax|react\/no-danger/)]))
  })
})

describe('the sink gate leaves alone', () => {
  // The complement: stops a match-everything rule from passing.
  it.each([
    ['setting text',                 'el.textContent = s'],
    ['a className',                  'el.className = s'],
    ['rendering a child',            'return <div>{userText}</div>'],
    ['an ordinary object key',       'const p = { title: s }; return <div {...p} />'],
    ['a function named in a string', "const name = 'eval'; return <div>{name}</div>"],
    ['reading innerText',            'const t = el.innerText; return <div>{t}</div>'],
  ])('%s', async (_name, code) => {
    expect(await lint(code)).toEqual([])
  })
})
