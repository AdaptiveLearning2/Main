import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join, resolve, sep } from 'node:path'
import { fileURLToPath } from 'node:url'
import { LineChart, Line } from 'recharts'
import AccessibleChart from './AccessibleChart'

const ROWS = [
  { day: 'Mon', focus: 40, bpm: null },
  { day: 'Tue', focus: 80, bpm: 72 },
]
const COLUMNS = [
  { key: 'focus', label: 'Focus', unit: '%' },
  { key: 'bpm', label: 'Heart rate', unit: ' bpm' },
]

const draw = (props = {}) => render(
  <div style={{ width: 400, height: 200 }}>
    <AccessibleChart headline="Signal trend." rows={ROWS} rowKey="day" rowLabel="Day"
                     columns={COLUMNS} {...props}>
      <LineChart data={ROWS}><Line dataKey="focus" /></LineChart>
    </AccessibleChart>
  </div>,
)

describe('AccessibleChart', () => {
  it('names the chart, with each series as a range', () => {
    draw()
    expect(screen.getByRole('img', { name: /Focus 40% to 80%/ })).toBeInTheDocument()
  })

  it('describes the summary and the table from one spec', () => {
    draw({ rows: [{ day: 'Mon', focus: 0.42 }, { day: 'Tue', focus: 0.78 }],
           columns: [{ key: 'focus', label: 'Focus', unit: '%', scale: v => v * 100 }] })

    expect(screen.getByRole('img', { name: /Focus 42% to 78%/ })).toBeInTheDocument()
    expect(screen.getByRole('table')).toHaveTextContent('42%')
  })

  it('leaves out a series with no readings rather than calling it zero', () => {
    draw({ rows: [{ day: 'Mon', focus: 40 }] })
    const name = screen.getByRole('img', { name: /Focus/ }).getAttribute('aria-label')
    expect(name).not.toMatch(/heart rate/i)
  })

  it('samples a long series rather than emitting a row per sample', () => {
    // 4Hz for an hour is ~14,000 rows.
    const many = Array.from({ length: 5000 }, (_, i) => ({ day: `d${i}`, focus: i % 100 }))
    draw({ rows: many })

    const rows = screen.getAllByRole('row')
    expect(rows.length).toBeLessThan(200)
    // And says so: a silently shortened table understates the session.
    expect(screen.getByRole('table')).toHaveTextContent(/sampled evenly across 5000/)
  })

  it('keeps the table outside the role="img" subtree', () => {
    // ARIA prunes roles inside an `img` but jsdom does not model that, so assert structure.
    draw()
    const chart = screen.getByRole('img', { name: /Focus/ })
    const table = screen.getByRole('table', { name: /Focus/ })
    expect(chart).not.toContainElement(table)
  })

  it('carries the rows, not just the summary', () => {
    draw()
    const table = screen.getByRole('table', { name: /Focus/ })
    expect(table).toHaveTextContent('Mon')
    expect(table).toHaveTextContent('40%')
  })

  it('says "not recorded" rather than leaving a cell blank', () => {
    draw()
    expect(screen.getByRole('table', { name: /Focus/ })).toHaveTextContent('not recorded')
  })

  it('still names the chart when there is no table to give', () => {
    // A sparkline has no row labels, so the summary is all there is.
    draw({ rowKey: undefined })
    expect(screen.getByRole('img', { name: /Focus/ })).toBeInTheDocument()
    expect(screen.queryByRole('table')).not.toBeInTheDocument()
  })
})

// ── the rule that keeps it centralised ──────────────────────────────────────

function jsxFiles(dir, out = []) {
  for (const name of readdirSync(dir)) {
    const full = join(dir, name)
    if (statSync(full).isDirectory()) jsxFiles(full, out)
    else if (name.endsWith('.jsx') && !name.endsWith('.test.jsx')) out.push(full)
  }
  return out
}

it('is the only place that renders a Recharts chart', () => {
  // Derived from source; matches chart components, not just `ResponsiveContainer`. Blind to hand-written `<svg>`.
  const CHART_IMPORTS = /\b(ResponsiveContainer|LineChart|BarChart|PieChart|AreaChart|RadarChart|ScatterChart|ComposedChart)\b/

  const root = resolve(fileURLToPath(import.meta.url), '..', '..', '..')
  const offenders = jsxFiles(root)
    // Path equality, not `endsWith`, or `MyAccessibleChart.jsx` exempts itself.
    .filter(f => f !== resolve(root, 'components', 'charts', 'AccessibleChart.jsx'))
    .filter(f => {
      const src = readFileSync(f, 'utf8')
      // Exempted by an import of the component, not a mention; `, { … }` allows named imports alongside.
      const imported =
        /^\s*import\s+AccessibleChart\s*(?:,\s*\{[^}]*\})?\s+from\s+['"][^'"]*AccessibleChart['"]/m
      return CHART_IMPORTS.test(src) && !imported.test(src)
    })
    .map(f => f.slice(root.length + 1).split(sep).join('/'))

  expect(offenders).toEqual([])
})

it('renders with no rows at all rather than throwing', () => {
  // The "was it sampled" check sits outside `sample()`'s `!rows` guard.
  expect(() => render(
    <div style={{ width: 400, height: 200 }}>
      <AccessibleChart headline="Nothing yet." rows={undefined} rowKey="day"
                       rowLabel="Day" columns={COLUMNS}>
        <LineChart data={[]}><Line dataKey="focus" /></LineChart>
      </AccessibleChart>
    </div>,
  )).not.toThrow()
})
