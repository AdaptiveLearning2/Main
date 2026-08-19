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
const TABLE = {
  rows: ROWS, rowKey: 'day', rowLabel: 'Day',
  columns: [{ key: 'focus', label: 'Focus', unit: '%' },
            { key: 'bpm', label: 'Heart rate', unit: ' bpm' }],
}

const draw = (props = {}) => render(
  <div style={{ width: 400, height: 200 }}>
    <AccessibleChart summary="Focus 40% to 80%." table={TABLE} {...props}>
      <LineChart data={ROWS}><Line dataKey="focus" /></LineChart>
    </AccessibleChart>
  </div>,
)

describe('AccessibleChart', () => {
  it('names the chart', () => {
    draw()
    expect(screen.getByRole('img', { name: /Focus 40% to 80%/ })).toBeInTheDocument()
  })

  it('keeps the table outside the role="img" subtree', () => {
    // The whole reason this is a component. WAI-ARIA's presentational-children
    // rule prunes every descendant role from an `img`, so a table nested inside
    // is invisible to real assistive technology -- and Testing Library, which
    // reads DOM attributes rather than modelling the accessibility tree, cannot
    // tell the difference. Structure is what gets asserted, because structure
    // is what the pruning depends on and the only part jsdom can see.
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
    // A blank cell is indistinguishable from a table that failed to render.
    draw()
    expect(screen.getByRole('table', { name: /Focus/ })).toHaveTextContent('not recorded')
  })

  it('still names the chart when there is no table to give', () => {
    // A sparkline has no meaningful row labels, so the summary is all there is.
    // It must still get a name rather than falling back to a bare `<svg>`.
    draw({ table: undefined })
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

it('is the only place that renders a ResponsiveContainer', () => {
  // Derived from the source rather than from a list someone maintains, which is
  // the same shape as the backend's `_MODE_AWARE` and `close_sites()` checks.
  //
  // Centralising the component does not by itself stop the next chart being
  // hand-assembled the old way -- and the old way's bug is invisible in the
  // browser *and* in a jsdom test, so nothing else would catch it. This is what
  // makes copying the pattern forward impossible: reach for Recharts' own
  // container and this fails, naming the file.
  // `fileURLToPath`, not `new URL(...).pathname` -- the latter yields
  // `/C:/...` on Windows, which `readdirSync` cannot open.
  const root = resolve(fileURLToPath(import.meta.url), '..', '..', '..')
  const offenders = jsxFiles(root)
    .filter(f => !f.endsWith('AccessibleChart.jsx'))
    .filter(f => readFileSync(f, 'utf8').includes('ResponsiveContainer'))
    .map(f => f.slice(root.length + 1).split(sep).join('/'))

  expect(offenders).toEqual([])
})
