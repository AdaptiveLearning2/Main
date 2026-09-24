import { useMemo } from 'react'
import { ResponsiveContainer } from 'recharts'
import ChartDataTable from './ChartDataTable'
import { describeChart } from './describeSeries'

/** Max `sr-only` table rows; longer data is sampled evenly and the caption says so. */
const MAX_TABLE_ROWS = 60

/** Rows evenly sampled down to `limit`, or unchanged if they fit. */
function sample(rows, limit) {
  if (!rows || rows.length <= limit) return rows || []
  const step = (rows.length - 1) / (limit - 1)
  return Array.from({ length: limit }, (_, i) => rows[Math.round(i * step)])
}

/**
 * A Recharts chart with a text alternative: `role="img"` + summary, plus an `sr-only` table
 * that is its sibling, never a child (ARIA prunes an img's descendants; jsdom cannot see that).
 * @param summary   explicit summary for non-series data (`sliceSpec` builds the spec; spread it).
 * @param columns   `[{key, label, unit, scale}]`; drives both the sentence and the table.
 */
export default function AccessibleChart({
  headline, summary, rows, rowKey, rowLabel, columns,
  height = '100%', className = 'h-full', children,
}) {
  const tableRows = useMemo(
    () => (columns && rowKey ? sample(rows, MAX_TABLE_ROWS) : null),
    [rows, rowKey, columns],
  )

  // Walks every row per series; pages poll.
  const text = useMemo(
    () => summary ?? describeChart(headline, rows, columns),
    [summary, headline, rows, columns],
  )

  return (
    <div className={className}>
      {tableRows && (
        <ChartDataTable
          // `?? 0`: `sample()` guards a nullish `rows`, this comparison must too.
          caption={tableRows.length < (rows?.length ?? 0)
            ? `${text} Table shows ${tableRows.length} rows sampled evenly across ${rows.length}.`
            : text}
          rows={tableRows} rowKey={rowKey} rowLabel={rowLabel} columns={columns}
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
