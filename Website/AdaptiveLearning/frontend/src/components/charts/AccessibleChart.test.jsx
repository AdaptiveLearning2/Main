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
    // They were separate literals at first and disagreed twice in one PR: a key
    // named `bpm` where the rows carried `heart_rate_bpm`, and raw 0..1 ratios
    // described with a `%` unit. Neither is visible on screen and neither
    // surface could contradict the other, because both were wrong at once.
    draw({ rows: [{ day: 'Mon', focus: 0.42 }, { day: 'Tue', focus: 0.78 }],
           columns: [{ key: 'focus', label: 'Focus', unit: '%', scale: v => v * 100 }] })

    expect(screen.getByRole('img', { name: /Focus 42% to 78%/ })).toBeInTheDocument()
    expect(screen.getByRole('table')).toHaveTextContent('42%')
  })

  it('leaves out a series with no readings rather than calling it zero', () => {
    // A gap in the line is not a zero, and the sentence has to make the same
    // distinction -- otherwise it claims a flat run the chart never drew.
    draw({ rows: [{ day: 'Mon', focus: 40 }] })
    const name = screen.getByRole('img', { name: /Focus/ }).getAttribute('aria-label')
    expect(name).not.toMatch(/heart rate/i)
  })

  it('samples a long series rather than emitting a row per sample', () => {
    // 4Hz for an hour is ~14,000 rows, every one built on every render for a
    // table nobody sighted sees -- and unusable for those who do.
    const many = Array.from({ length: 5000 }, (_, i) => ({ day: `d${i}`, focus: i % 100 }))
    draw({ rows: many })

    const rows = screen.getAllByRole('row')
    expect(rows.length).toBeLessThan(200)
    // And says so, because a silently shortened table claims the session was
    // shorter than it was.
    expect(screen.getByRole('table')).toHaveTextContent(/sampled evenly across 5000/)
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
  // Derived from the source rather than from a maintained list, which is the
  // distinction that matters: the backend's `close_sites()` discovers its own
  // members the same way, and it is the only precedent worth citing. An earlier
  // version of this comment also named `_MODE_AWARE` -- wrongly, since that one
  // *is* a hand-written list, and CLAUDE.md records it being extended one
  // endpoint at a time across five review rounds because of exactly that.
  //
  // Centralising the component does not by itself stop the next chart being
  // hand-assembled the old way, and the old way's bug is invisible in the
  // browser *and* in a jsdom test, so nothing else would catch it.
  //
  // Matched on the chart *components*, not on `ResponsiveContainer` alone: a
  // chart given explicit width and height needs no container, so keying on that
  // one import let a whole class of hand-rolled chart through with CI green.
  // It still cannot see a hand-written `<svg>`, which is the honest limit of a
  // source check -- noted rather than papered over.
  const CHART_IMPORTS = /\b(ResponsiveContainer|LineChart|BarChart|PieChart|AreaChart|RadarChart|ScatterChart|ComposedChart)\b/

  const root = resolve(fileURLToPath(import.meta.url), '..', '..', '..')
  const offenders = jsxFiles(root)
    // Resolved path equality, not `endsWith`: a file named
    // `MyAccessibleChart.jsx` would otherwise exempt itself from the rule by
    // its name alone.
    .filter(f => f !== resolve(root, 'components', 'charts', 'AccessibleChart.jsx'))
    .filter(f => {
      const src = readFileSync(f, 'utf8')
      // The chart tree itself is passed in as children, so a page naming
      // `<LineChart>` is fine *provided* it goes through the component.
      // The exemption is an *import* of the component, not the string
      // appearing anywhere: `// TODO: move this to AccessibleChart` in a
      // comment would otherwise excuse the file it is written in, which is
      // exactly the file most likely to carry that comment.
      // The optional `, { … }` matters: `import AccessibleChart, { foo } from`
      // is a shape this codebase already uses elsewhere, and a regex requiring
      // a bare default import would fail to match it — flagging a fully
      // compliant file as an offender. A guard that produces false accusations
      // gets switched off, which costs more than the gap it was closing.
      const imported =
        /^\s*import\s+AccessibleChart\s*(?:,\s*\{[^}]*\})?\s+from\s+['"][^'"]*AccessibleChart['"]/m
      return CHART_IMPORTS.test(src) && !imported.test(src)
    })
    .map(f => f.slice(root.length + 1).split(sep).join('/'))

  expect(offenders).toEqual([])
})

it('renders with no rows at all rather than throwing', () => {
  // `sample()` guards `!rows` and returns `[]`, so moving the "was it sampled"
  // derivation out of it moved that check away from the guard: the caption
  // compared `tableRows.length < rows.length` and threw on a nullish `rows`.
  //
  // Every live call site guards `rows` before rendering, so it was unreachable
  // — but the shape this replaced could not reach it at all, and a component
  // that crashes on absent data is the wrong default for one whose whole job is
  // describing data that may not be there.
  expect(() => render(
    <div style={{ width: 400, height: 200 }}>
      <AccessibleChart headline="Nothing yet." rows={undefined} rowKey="day"
                       rowLabel="Day" columns={COLUMNS}>
        <LineChart data={[]}><Line dataKey="focus" /></LineChart>
      </AccessibleChart>
    </div>,
  )).not.toThrow()
})
