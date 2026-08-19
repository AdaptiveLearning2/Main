import { ResponsiveContainer } from 'recharts'
import ChartDataTable from './ChartDataTable'

/**
 * A Recharts chart with a text alternative, assembled correctly.
 *
 * Recharts emits bare `<svg>` with no accessible name and no structure a
 * screen reader can walk, so every chart in this product announced as nothing.
 * Two things fix that, and the second one has a trap in it:
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
 * `img` unconditionally, so a table nested inside is invisible to real
 * assistive technology — and Testing Library, which reads DOM attributes rather
 * than modelling the accessibility tree, reports it as present either way. The
 * first version of this pattern got exactly that wrong: the table built so a
 * screen-reader user could read the data was pruned, and the test written to
 * prove otherwise could not see the difference.
 *
 * That is the whole reason this is a component rather than a documented recipe.
 * The nesting is invisible when wrong, in the browser and in the test, so a
 * call site that hand-assembles it has nothing to fail against. Here it is
 * structural: there is no way to pass the table in that puts it inside the
 * wrapper. `charts.test.jsx` holds the counterpart rule — nothing outside this
 * file may render a `ResponsiveContainer` — so the next chart cannot rebuild
 * the pattern by hand and get it wrong again.
 *
 * @param summary  the sentence that replaces the picture. Also the table's caption.
 * @param table    `{rows, rowKey, rowLabel, columns}`, or omitted for a chart
 *                 whose summary genuinely is all there is to say.
 * @param height   passed to `ResponsiveContainer`; `"100%"` needs a sized parent.
 */
export default function AccessibleChart({
  summary, table, height = '100%', className = 'h-full', children,
}) {
  return (
    <div className={className}>
      {table && <ChartDataTable caption={summary} {...table} />}
      <div className="h-full" role="img" aria-label={summary}>
        <ResponsiveContainer width="100%" height={height}>
          {children}
        </ResponsiveContainer>
      </div>
    </div>
  )
}
