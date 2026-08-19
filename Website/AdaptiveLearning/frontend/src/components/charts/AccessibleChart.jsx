import { useMemo } from 'react'
import { ResponsiveContainer } from 'recharts'
import ChartDataTable from './ChartDataTable'
import { describeChart } from './describeSeries'

/** How many rows the `sr-only` table will render.
 *
 * A session's cognitive channel arrives at 4Hz, so an hour is ~14,000 rows —
 * every one a `<tr>` with a cell per series, built on every render, for a table
 * nobody sighted ever sees. That is a real cost paid by every visitor to make
 * the page usable for one, and past a few dozen rows it stops being usable for
 * them either: nobody arrows through fourteen thousand rows to find a number.
 *
 * Evenly sampled rather than truncated, so the table still spans the whole
 * session, and `ChartDataTable`'s caption says it was sampled — a silently
 * shortened table is a claim that the session was shorter than it was.
 */
const MAX_TABLE_ROWS = 60

function sample(rows, limit) {
  if (!rows || rows.length <= limit) return { rows: rows || [], sampled: false }
  const step = (rows.length - 1) / (limit - 1)
  return {
    rows: Array.from({ length: limit }, (_, i) => rows[Math.round(i * step)]),
    sampled: true,
  }
}

/**
 * A Recharts chart with a text alternative, assembled correctly.
 *
 * Recharts emits bare `<svg>` with no accessible name and no structure a
 * screen reader can walk, so every chart in this product announced as nothing.
 * Two things fix that, and the second has a trap in it:
 *
 * - **`role="img"` with a summary**, so a reader gets a sentence instead of
 *   hundreds of meaningless `<path>` nodes.
 * - **A `sr-only` table** carrying the numbers, because the summary alone is a
 *   headline with the data thrown away. A sighted reader can pick a Tuesday out
 *   of a line; without the table a screen-reader user gets a range and no way
 *   to ask which day was which.
 *
 * **The table must be a sibling of the `role="img"` element, never a child.**
 * WAI-ARIA's presentational-children rule prunes every descendant role from an
 * `img`, so a table nested inside is invisible to real assistive technology —
 * and Testing Library, which reads DOM attributes rather than modelling the
 * accessibility tree, reports it as present either way. A hand-assembled call
 * site had nothing to fail against, in the browser or in CI, which is why this
 * is a component and not a documented recipe.
 *
 * **`columns` drives both the sentence and the table.** They were separate
 * literals at first, and disagreed twice in one PR — a key named `bpm` where
 * the rows carry `heart_rate_bpm`, and raw 0..1 ratios described with a `%`
 * unit. One spec cannot disagree with itself; see `describeSeries.js`.
 *
 * @param headline  the sentence's opening clause; series ranges are appended.
 * @param summary   an explicit summary, for charts whose data is not a series
 *                  (`describeSlices` builds the categorical one).
 * @param rows      the chart's data, also the table's.
 * @param columns   `[{key, label, unit, scale}]` — one spec, both surfaces.
 * @param height    passed to `ResponsiveContainer`; `"100%"` needs a sized parent.
 */
export default function AccessibleChart({
  headline, summary, rows, rowKey, rowLabel, columns,
  height = '100%', className = 'h-full', children,
}) {
  const table = useMemo(() => {
    if (!columns || !rowKey) return null
    return { ...sample(rows, MAX_TABLE_ROWS), rowKey, rowLabel, columns }
  }, [rows, rowKey, rowLabel, columns])

  // Memoised for the same reason as the table: this walks every row once per
  // series, and it runs on every render of a page that polls.
  const text = useMemo(
    () => summary ?? describeChart(headline, rows, columns),
    [summary, headline, rows, columns],
  )

  return (
    <div className={className}>
      {table && (
        <ChartDataTable
          caption={table.sampled
            ? `${text} Table shows ${table.rows.length} rows sampled evenly across ${rows.length}.`
            : text}
          rows={table.rows} rowKey={table.rowKey} rowLabel={table.rowLabel}
          columns={table.columns}
        />
      )}
      <div className="h-full" role="img" aria-label={text}>
        <ResponsiveContainer width="100%" height={height}>
          {children}
        </ResponsiveContainer>
      </div>
    </div>
  )
}
