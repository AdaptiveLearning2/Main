import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { expect, it } from 'vitest'

// A layout may not key its page transition on `window.location.pathname`:
// that value isn't React state, so a client-side navigation re-renders with
// the same key and the enter animation never plays.
//
// The layout list comes from scanning the directory, not a hand-kept list,
// so a new layout copied from an old one can't slip past this check.
//
// This is a source check rather than a rendered-output check, since the bug
// is about how a layout is written, not something observable at runtime
// without driving a full navigation per layout.

const HERE = path.dirname(fileURLToPath(import.meta.url))

const layouts = fs.readdirSync(HERE)
  .filter(f => f.endsWith('Layout.jsx'))
  .map(f => [f, fs.readFileSync(path.join(HERE, f), 'utf8')])

it('finds the layouts', () => {
  // Guards against a rename that stops matching `*Layout.jsx`, which would
  // make every check below vacuously pass.
  expect(layouts.length).toBeGreaterThanOrEqual(4)
})

it.each(layouts.map(([name]) => name))(
  '%s does not key its transition on window.location', (name) => {
    const [, source] = layouts.find(([f]) => f === name)
    // Strip comments first, since they're allowed to mention the pattern
    // by name when explaining the fix.
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
    // AuthLayout and MainLayout may have no transition at all; this only
    // checks layouts that do key on a path.
    if (!/key=\{\s*pathname\s*\}/.test(source)) return
    expect(source).toMatch(/useLocation/)
  })
