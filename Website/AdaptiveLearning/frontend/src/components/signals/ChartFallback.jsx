/**
 * The text a chart is, for anyone who cannot see it.
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
export function ChartDataTable({ caption, rows, rowKey, rowLabel, columns }) {
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
                {typeof r[c.key] === 'number'
                  ? `${Math.round(r[c.key])}${c.unit ?? ''}`
                  : 'not recorded'}
              </td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  )
}
