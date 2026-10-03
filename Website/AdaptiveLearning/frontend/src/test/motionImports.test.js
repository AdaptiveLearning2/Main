/** The motion guard, linted through the real `eslint.sinks.config.js` CI blocks on, and read from the report. */
import { describe, it, expect, beforeAll } from 'vitest'
import { ESLint } from 'eslint'
import { parse } from '@babel/parser'
import { domAnimation, domMax, isValidMotionProp } from 'framer-motion'
import fs from 'node:fs'
import path from 'node:path'
import url from 'node:url'
import { UNSERVED_PROPS } from '../../eslint.motion.js'

const ROOT = path.resolve(path.dirname(url.fileURLToPath(import.meta.url)), '../..')
const IMPORTS = ['no-restricted-imports']
const SYNTAX = ['no-restricted-syntax']

let eslint
// The warm-up lint loads config and plugins under its own timeout, outside the first test's 5 s budget.
beforeAll(async () => {
  eslint = new ESLint({ cwd: ROOT, overrideConfigFile: path.join(ROOT, 'eslint.sinks.config.js') })
  await lint('const warm = 1')
}, 60_000)

/** The rule ids reported for one snippet, linted as a file under `src/`. */
async function lint(code) {
  const [result] = await eslint.lintText(`${code}\n`, { filePath: path.join(ROOT, 'src/__probe.jsx') })
  return result.messages.map(m => m.ruleId)
}

describe('the motion guard flags', () => {
  it.each([
    ['motion, named',                  "import { motion } from 'framer-motion'", IMPORTS],
    ['motion, renamed',                "import { motion as Motion } from 'framer-motion'", IMPORTS],
    ['a namespace import',             "import * as fm from 'framer-motion'", IMPORTS],
    ['a re-export',                    "export { motion } from 'framer-motion'", IMPORTS],
    ['motion from motion/react',       "import { motion } from 'motion/react'", IMPORTS],
    ['a motion/react namespace',       "import * as fm from 'motion/react'", IMPORTS],
    ['a framer-motion/client element', "import { div } from 'framer-motion/client'", IMPORTS],
    ['a motion/react-client namespace', "import * as motion from 'motion/react-client'", IMPORTS],
    ['a dynamic framer-motion',        "export const load = () => import('framer-motion')", SYNTAX],
    ['a dynamic motion/react',         "export const load = () => import('motion/react')", SYNTAX],
    ['a dynamic framer-motion/client', "export const load = () => import('framer-motion/client')", SYNTAX],
    ['a dynamic motion/react-client',  "export const load = () => import('motion/react-client')", SYNTAX],
  ])('%s', async (_name, code, rules) => {
    expect(await lint(code)).toEqual(rules)
  })

  it.each(UNSERVED_PROPS)('%s on an m or motion element', async prop => {
    expect(await lint(`export const P = () => <m.div ${prop} />`)).toEqual(SYNTAX)
    expect(await lint(`export const P = () => <motion.span ${prop} />`)).toEqual(SYNTAX)
  })
})

describe('the motion guard leaves alone', () => {
  // The complement: stops a match-everything rule from passing.
  it.each([
    ['m and its helpers',            "import { m, AnimatePresence, LazyMotion, domAnimation } from 'framer-motion'"],
    ['m from motion/react',          "import { m } from 'motion/react'"],
    ['the m-only module',            "import * as m from 'framer-motion/m'"],
    ['a dynamic m-only module',      "export const load = () => import('framer-motion/m')"],
    ['the module named in a string', "export const name = 'framer-motion'"],
    ['the animation props',          'export const P = () => <m.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} transition={{ duration: 0.2 }} variants={{}} />'],
    ['the gesture animations',       'export const P = () => <m.button whileHover={{ scale: 1.02 }} whileTap={{ scale: 0.98 }} whileInView={{ opacity: 1 }} onHoverStart={() => {}} />'],
    ['native drag on a DOM element', 'export const P = f => <div draggable onDragStart={f} onDrag={f} onDragEnd={f} />'],
    ['draggable on an m element',    'export const P = () => <m.div draggable />'],
    ["another component's layout",   'export const P = () => <Grid layout="row" />'],
  ])('%s', async (_name, code) => {
    expect(await lint(code)).toEqual([])
  })
})

/** Member names of an interface in motion-dom's published types. */
function members(ast, name) {
  const decl = ast.program.body.find(n => n.type === 'TSInterfaceDeclaration' && n.id.name === name)
  expect(decl, name).toBeDefined()
  return decl.body.body.map(member => member.key.name ?? member.key.value)
}

describe('the prop list matches the installed framer-motion', () => {
  it('drag, pan and layout are exactly what domMax adds to domAnimation', () => {
    expect(Object.keys(domMax).filter(k => !(k in domAnimation)).sort()).toEqual(['drag', 'layout', 'pan'])
  })

  it('names every prop of those three features, from the types', () => {
    const types = fs.readFileSync(path.join(ROOT, 'node_modules/motion-dom/dist/index.d.ts'), 'utf8')
    const ast = parse(types, { sourceType: 'module', plugins: ['typescript'] })
    const expected = [
      ...members(ast, 'MotionNodeDraggableOptions'), ...members(ast, 'MotionNodeDragHandlers'),
      ...members(ast, 'MotionNodePanHandlers'), ...members(ast, 'MotionNodeLayoutOptions'),
      // The layout lifecycles sit among the animation callbacks.
      ...members(ast, 'MotionNodeEventOptions').filter(n => n.includes('Layout')),
    ]
    expect([...UNSERVED_PROPS].sort()).toEqual([...new Set(expected)].sort())
  })

  it('every listed prop but a data- attribute is consumed by motion, never passed to the DOM', () => {
    expect(UNSERVED_PROPS.filter(p => !isValidMotionProp(p))).toEqual(['data-framer-portal-id'])
  })
})

it('no source file breaks the motion guard', async () => {
  const results = await eslint.lintFiles(['src/**/*.{js,jsx}'])
  const found = results.flatMap(r => r.messages
    .map(m => `${path.relative(ROOT, r.filePath)}:${m.line} ${m.ruleId} ${m.message}`))
  expect(results.length).toBeGreaterThan(100)
  expect(found).toEqual([])
}, 60_000)
