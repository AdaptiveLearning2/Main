/**
 * The rows behind a chart, for screen readers only.
 *
 * Rendered by `AccessibleChart`, which is what places it -- *outside* the
 * `role="img"` wrapper, since ARIA prunes an `img`'s descendants and would make
 * this table invisible to the readers it exists for. Use that rather than this
 * directly; the placement is the part that is easy to get wrong and impossible
 * to see when it is.
 *
 * Recharts renders a bare `<svg>` with no accessible name, so the weekly
 * signal panel announced as nothing at all. Two parts, both needed:
 *
 * - **`role="img"` with a summary** on the chart container, stopping a reader
 *   from tabbing through meaningless `<path>` nodes.
 * - **A `sr-only` table** carrying the actual numbers, so a screen-reader
 *   user isn't left with just "focus ranged 42% to 78%".
 *
 * Not `aria-hidden` on the SVG, since some screen readers still surface
 * titled SVG content alongside the table either way.
 */

/** The rows behind a chart, for screen readers only.
 *
 * `columns` is `[{key, label, unit}]`. A cell with no number reads as "not
 * recorded" rather than a blank, which would look like a broken table.
 */
import { readValue } from './describeSeries'

/** One cell's text.
 *
 * Through `readValue`, so the scale applied here is the same one the summary
 * sentence used — the two disagreeing is the bug the shared spec exists to make
 * impossible.
 */
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
