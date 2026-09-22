/**
 * Every spelling of every XSS sink, linted through the real gate config.
 *
 * `npm run lint:sinks` is what CI blocks on, and it is only as good as its
 * selectors. Those were hand-checked twice and were wrong twice: first the
 * member-assignment route to `dangerouslySetInnerHTML` (with a comment saying
 * otherwise), then every *quoted* spelling, because `el.innerHTML` puts the
 * name in an Identifier's `.name` and `el['innerHTML']` puts it in a string
 * Literal's `.value`. `<div {...{'dangerouslySetInnerHTML': {__html: x}}} />`
 * passed a green blocking gate on one pair of quotes.
 *
 * Reading the rules is what failed both times, so this reads the *report*.
 * It runs ESLint over source text with `eslint.sinks.config.js` -- the same
 * file CI uses, not a copy -- and asserts each form is flagged. A selector
 * that stops matching fails here rather than going quiet.
 *
 * `npm run lint:sinks` still has to exist: this proves the rules catch the
 * forms, that proves they are applied to the tree.
 */
import { describe, it, expect, beforeAll } from 'vitest'
import { ESLint } from 'eslint'
import path from 'node:path'
import url from 'node:url'

const ROOT = path.resolve(path.dirname(url.fileURLToPath(import.meta.url)), '../..')

let eslint
beforeAll(() => {
  eslint = new ESLint({
    cwd: ROOT,
    overrideConfigFile: path.join(ROOT, 'eslint.sinks.config.js'),
  })
})

/** The rule ids reported for one snippet, linted as a file under `src/`. */
async function lint(code) {
  const [result] = await eslint.lintText(`export function probe(el, s, props, userText) {\n${code}\n}\n`,
                                         { filePath: path.join(ROOT, 'src/__probe.jsx') })
  return result.messages
}

// Both spellings of each sink. The quoted column is the one that was open:
// nothing about a sink changes when its name is written as a string.
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
  // The complement, and the half that keeps this from being satisfiable by a
  // rule matching everything. Each is ordinary code this app writes.
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
