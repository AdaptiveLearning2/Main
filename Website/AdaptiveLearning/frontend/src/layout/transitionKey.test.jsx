import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { expect, it } from 'vitest'

// No layout keys its transition on `window.location.pathname`, which is not React state.

const HERE = path.dirname(fileURLToPath(import.meta.url))

const layouts = fs.readdirSync(HERE)
  .filter(f => f.endsWith('Layout.jsx'))
  .map(f => [f, fs.readFileSync(path.join(HERE, f), 'utf8')])

it('finds the layouts', () => {
  // Otherwise a rename would make every check below pass vacuously.
  expect(layouts.length).toBeGreaterThanOrEqual(4)
})

it.each(layouts.map(([name]) => name))(
  '%s does not key its transition on window.location', (name) => {
    const [, source] = layouts.find(([f]) => f === name)
    // Strip comments first: they may mention the pattern.
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
    // Only layouts that key on a path are checked.
    if (!/key=\{\s*pathname\s*\}/.test(source)) return
    expect(source).toMatch(/useLocation/)
  })
