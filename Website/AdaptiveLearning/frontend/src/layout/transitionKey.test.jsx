import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { expect, it } from 'vitest'

// A layout may not key its page transition on `window.location.pathname`.
//
// That value is read off the browser during render. It is not state, nothing
// subscribes to it, and React is never told it changed -- so a client-side
// navigation re-renders with the *same* key, `motion.div` sees one continuous
// element, and the enter animation never plays. The page changes; it just does
// not transition.
//
// Derived from the directory rather than a list of the layouts that had it,
// because all four did, and the fifth will be written by copying one of them.
// A hand-kept list is the thing that lets the copy through -- the same
// exhaustiveness pattern the backend uses for its close sites and mode-aware
// endpoints.
//
// It is a source check, deliberately. What goes wrong is a layout being
// *written* with the old form, not a fixed one regressing at runtime, and
// asserting on the rendered output would mean driving a router through a
// navigation per layout to observe an animation that framer-motion owns.

const HERE = path.dirname(fileURLToPath(import.meta.url))

const layouts = fs.readdirSync(HERE)
  .filter(f => f.endsWith('Layout.jsx'))
  .map(f => [f, fs.readFileSync(path.join(HERE, f), 'utf8')])

it('finds the layouts', () => {
  // Guards the guard: a rename that stopped matching `*Layout.jsx` would make
  // every assertion below vacuous and green.
  expect(layouts.length).toBeGreaterThanOrEqual(4)
})

it.each(layouts.map(([name]) => name))(
  '%s does not key its transition on window.location', (name) => {
    const [, source] = layouts.find(([f]) => f === name)
    // Comments are allowed to name it -- ParentLayout's explains the fix, and
    // stripping the explanation to satisfy a grep would be the wrong trade.
    const code = source
      .replace(/\/\*[\s\S]*?\*\//g, '')
      .split('\n')
      .filter(line => !line.trim().startsWith('//'))
      .join('\n')

    expect(code).not.toMatch(/key=\{\s*window\.location/)
  })

it.each(layouts.filter(([, s]) => s.includes('key={')).map(([name]) => name))(
  '%s subscribes to the route it keys on', (name) => {
    const [, source] = layouts.find(([f]) => f === name)
    // AuthLayout and MainLayout may legitimately have no transition at all;
    // this only asks that a layout which keys on a path got that path from the
    // router rather than from the browser.
    if (!/key=\{\s*pathname\s*\}/.test(source)) return
    expect(source).toMatch(/useLocation/)
  })
