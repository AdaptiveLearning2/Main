/**
 * The rows behind a chart, for screen readers only.
 *
 * Rendered by `AccessibleChart`, which is what places it -- *outside* the
 * `role="img"` wrapper, since ARIA prunes an `img`'s descendants and would make
 * this table invisible to the readers it exists for. Use that rather than this
 * directly; the placement is the part that is easy to get wrong and impossible
 * to see when it is.
 *
 * Recharts renders bare `<svg>` with no accessible name and no structure a
 * screen reader can walk, so the weekly signal panel — the whole of what a
 * parent or teacher is shown about a child's week — announced as nothing at
 * all. The stat tiles above it are readable; the trend and the distribution
 * were not.
 *
 * Two parts, and both are needed:
 *
 * - **`role="img"` with a summary** on the chart container. The role is what
 *   stops a reader tabbing through hundreds of meaningless `<path>` nodes, and
 *   the label is the sentence that replaces them. On its own it is a headline
 *   with the data thrown away.
 * - **A `sr-only` table** carrying the numbers. A sighted reader can pick a
 *   Tuesday out of a line; without this, a screen-reader user gets "focus
 *   ranged 42% to 78%" and no way to ask which day was which.
 *
 * Deliberately not `aria-hidden` on the SVG with a table beside it: some
 * screen readers still surface titled SVG content, and a chart that announces
 * both its own noise and a table is worse than one that announces the table.
 */

/** The rows behind a chart, for screen readers only.
 *
 * `columns` is `[{key, label, unit}]`. A cell with no number reads as
 * "not recorded" rather than as a blank, which is indistinguishable from a
 * table that failed to render.
 */
import { readValue } from './describeSeries'

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
              <td key={c.key}>
                {/* Through `readValue`, so the scale applied here is the same
                    one the summary sentence used -- the two disagreeing is the
                    bug this spec exists to make impossible. */}
                {(() => {
                  const v = readValue(r, c)
                  return v === null ? 'not recorded' : `${Math.round(v)}${c.unit ?? ''}`
                })()}
              </td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  )
}
