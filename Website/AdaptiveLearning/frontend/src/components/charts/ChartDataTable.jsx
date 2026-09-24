/**
 * The rows behind a chart, for screen readers only. Render it through
 * `AccessibleChart`, which places it outside the `role="img"` wrapper.
 * An empty cell reads "not recorded", never blank.
 */
import { readValue } from './describeSeries'

/** One cell's text, through `readValue` so it matches the summary sentence. */
function cellText(row, col) {
  const v = readValue(row, col)
  return v === null ? 'not recorded' : `${Math.round(v)}${col.unit ?? ''}`
}

export default function ChartDataTable({ caption, rows, rowKey, rowLabel, columns }) {
  return (
    <table className="sr-only">
      <caption>{caption}</caption>
      <thead>
        <tr>
          <th scope="col">{rowLabel}</th>
          {columns.map(c => <th key={c.key} scope="col">{c.label}</th>)}
        </tr>
      </thead>
      <tbody>
        {rows.map((r, i) => (
          <tr key={r[rowKey] ?? i}>
            <th scope="row">{r[rowKey]}</th>
            {columns.map(c => (
              <td key={c.key}>{cellText(r, c)}</td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  )
}
