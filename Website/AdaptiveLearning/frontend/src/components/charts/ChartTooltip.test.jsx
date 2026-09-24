/** Every Recharts tooltip goes through ChartTooltip: the stock one is unreadable in dark mode. */
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join, relative, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, it, expect } from 'vitest'
import { axisLabelFill, tooltipStyles } from './chartTooltipStyles'

const SRC = resolve(fileURLToPath(import.meta.url), '..', '..', '..')
const rel = f => relative(SRC, f).replaceAll('\\', '/')
const walk = (dir) => readdirSync(dir).flatMap(name => {
  const full = join(dir, name)
  return statSync(full).isDirectory() ? walk(full)
    : full.endsWith('.jsx') && !full.includes('.test.') ? [full] : []
})

/** Every `label=` value on an `<XAxis`/`<YAxis`, scanned at brace depth since a regex cannot cross a nested `}`. */
function axisLabels(src) {
  const out = []
  const open = /<[XY]Axis\b/g
  let m
  while ((m = open.exec(src))) {
    // The element runs to the first `>` at brace depth 0.
    let i = m.index, depth = 0
    for (; i < src.length; i++) {
      const c = src[i]
      if (c === '{') depth++
      else if (c === '}') depth--
      else if (c === '>' && depth === 0) break
    }
    const element = src.slice(m.index, i + 1)
    const at = element.search(/\blabel=/)
    if (at < 0) continue
    let j = at + 'label='.length
    if (element[j] === '"' || element[j] === "'") {
      const end = element.indexOf(element[j], j + 1)
      out.push(element.slice(j, end + 1))
    } else if (element[j] === '{') {
      let d = 0, k = j
      for (; k < element.length; k++) {
        if (element[k] === '{') d++
        else if (element[k] === '}' && --d === 0) break
      }
      out.push(element.slice(j, k + 1))
    }
  }
  return out
}

describe('ChartTooltip', () => {
  it('picks readable text for each theme rather than inheriting it', () => {
    const light = tooltipStyles(false)
    const dark = tooltipStyles(true)
    // The label is styled explicitly, not inherited from the page.
    expect(light.contentStyle.backgroundColor).toBe('#ffffff')
    expect(light.labelStyle.color).toBe('#111827')
    expect(dark.contentStyle.backgroundColor).toBe('#111827')
    expect(dark.labelStyle.color).toBe('#f9fafb')
    expect(dark.labelStyle.color).not.toBe(dark.contentStyle.backgroundColor)
  })

  it('chooses an axis-label fill per theme rather than the library grey', () => {
    // Recharts' default #808080 is 3.95:1 on white, under AA.
    const lum = hex => {
      const c = [1, 3, 5].map(i => parseInt(hex.slice(i, i + 2), 16) / 255)
        .map(v => (v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4))
      return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]
    }
    const ratio = (a, b) => { const [h, l] = [lum(a), lum(b)].sort((x, y) => y - x); return (h + 0.05) / (l + 0.05) }
    expect(ratio(axisLabelFill(false), '#ffffff')).toBeGreaterThanOrEqual(4.5)   // light panel
    expect(ratio(axisLabelFill(true), '#111827')).toBeGreaterThanOrEqual(4.5)    // dark:bg-gray-900
    expect(ratio('#808080', '#ffffff')).toBeLessThan(4.5)                        // what "unset" would be
  })

  it('gives every axis label a fill, since the library default fails AA', () => {
    // A source check: charts do not lay out under jsdom.
    const charted = walk(SRC)
      .filter(f => /from 'recharts'/.test(readFileSync(f, 'utf8')))
    const offenders = charted
      .flatMap(f => axisLabels(readFileSync(f, 'utf8'))
        .filter(l => !/\bfill:/.test(l))
        .map(l => `${rel(f)}: ${l}`))
    expect(offenders).toEqual([])
    // `[]` is also what an empty walk gives, so pin the scanned set via the same walk.
    expect(charted.length).toBeGreaterThan(5)
    const focus = charted.find(f => rel(f) === 'components/analytics/FocusAccuracy.jsx')
    expect(focus).toBeDefined()
    expect(axisLabels(readFileSync(focus, 'utf8'))).toHaveLength(2)
  })

  it('finds an axis label however it is written', () => {
    // An unmatched label is never checked.
    const src = `
      <XAxis dataKey="x" label={{ value: 'A', fill: '#374151' }} />
      <YAxis label={{ value: 'B', position: 'insideLeft' }} />
      <XAxis label={{ value: 'C', style: { fontSize: 11 } }} />
      <YAxis label={{ value: \`\${unit} (%)\` }} />
      <XAxis label="D" />
      <YAxis label={{ value: 'answers they fill in' }} />
      <Gauge label="not an axis" />
    `
    const found = axisLabels(src)
    expect(found).toHaveLength(6)
    // `fill:` the key, not `fill` the word in label text.
    expect(found.filter(l => !/\bfill:/.test(l))).toHaveLength(5)
  })

  it('is the only file that renders a Recharts Tooltip', () => {
    const files = walk(SRC)
    // Not vacuous: the walk found the tree, and this component in it.
    expect(files.length).toBeGreaterThan(5)
    expect(files.map(rel)).toContain('components/charts/ChartTooltip.jsx')
    const offenders = files
      .filter(f => rel(f) !== 'components/charts/ChartTooltip.jsx')
      .filter(f => {
        const src = readFileSync(f, 'utf8')
        // Import or render: a local alias passes the first check, not the second.
        return /import\s*{[^}]*\bTooltip\b[^}]*}\s*from\s*'recharts'/.test(src)
          || /<Tooltip[\s/>]/.test(src)
      })
      .map(rel)
    expect(offenders).toEqual([])
  })
})
