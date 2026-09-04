/**
 * Every Recharts tooltip goes through ChartTooltip, and a test enforces it --
 * the same shape as AccessibleChart's guard, for the same reason: the stock
 * tooltip is a white box whose text inherits the page colour, unreadable in
 * dark mode, and a call site that reaches for `Tooltip` directly gets that
 * back silently.
 */
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join, relative, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, it, expect } from 'vitest'
import { tooltipStyles } from './chartTooltipStyles'

const SRC = resolve(fileURLToPath(import.meta.url), '..', '..', '..')
const rel = f => relative(SRC, f).replaceAll('\\', '/')
const walk = (dir) => readdirSync(dir).flatMap(name => {
  const full = join(dir, name)
  return statSync(full).isDirectory() ? walk(full)
    : full.endsWith('.jsx') && !full.includes('.test.') ? [full] : []
})

describe('ChartTooltip', () => {
  it('picks readable text for each theme rather than inheriting it', () => {
    const light = tooltipStyles(false)
    const dark = tooltipStyles(true)
    // Light: dark text on white. Dark: light text on a dark panel. The label
    // is the line that names the bar, so it is styled explicitly, not left to
    // the page's text colour.
    expect(light.contentStyle.backgroundColor).toBe('#ffffff')
    expect(light.labelStyle.color).toBe('#111827')
    expect(dark.contentStyle.backgroundColor).toBe('#111827')
    expect(dark.labelStyle.color).toBe('#f9fafb')
    expect(dark.labelStyle.color).not.toBe(dark.contentStyle.backgroundColor)
  })

  it('is the only file that renders a Recharts Tooltip', () => {
    const offenders = walk(SRC)
      .filter(f => rel(f) !== 'components/charts/ChartTooltip.jsx')
      .filter(f => {
        const src = readFileSync(f, 'utf8')
        // Importing `Tooltip` from recharts, or rendering `<Tooltip` at all --
        // a local alias would satisfy the first check and not the second.
        return /import\s*{[^}]*\bTooltip\b[^}]*}\s*from\s*'recharts'/.test(src)
          || /<Tooltip[\s/>]/.test(src)
      })
      .map(rel)
    expect(offenders).toEqual([])
  })
})
