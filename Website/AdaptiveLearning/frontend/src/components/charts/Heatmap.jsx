/**
 * A matrix of accuracies, rendered as a real table with coloured cells.
 *
 * **Deliberately not an `AccessibleChart`, and that is not a gap in the rule.**
 * `AccessibleChart` exists because Recharts emits a bare `<svg>` with nothing a
 * screen reader can walk, so it pairs the picture with an `sr-only` table. A
 * matrix has no series to plot: it *is* the table. Wrapping one in a chart
 * would mean drawing a grid of squares and then reconstructing the table that
 * was already the honest markup, so the shading here is an enhancement on top
 * of real `<th scope>`/`<td>` structure rather than a substitute for it.
 *
 * The rule that does still apply is the one underneath the component: a chart
 * must not announce as nothing. This announces as a table with row and column
 * headers, which is strictly more navigable than the sr-only copy would be —
 * a reader can ask for "Alice, geometry" directly.
 *
 * `AccessibleChart.test.jsx` cannot see this file (its guard matches Recharts
 * imports, and there are none). That is the same stated blind spot it has for
 * a hand-written `<svg>`, so this is on review rather than on CI — hence the
 * length of this comment.
 *
 * Three cell states, kept apart because two of them look identical if you only
 * think about colour:
 *
 *   - a number      → shaded, and read out as a percentage
 *   - `null`        → "not attempted". Not shaded, and *not* the zero colour.
 *                     A topic nobody was served is not a topic they failed.
 *   - below `minAttempts` → shaded, but marked thin. The figure is real and is
 *                     shown; what is withheld is the confidence, because one
 *                     answer reads as 0% or 100% and colours as strongly as
 *                     four hundred do.
 */

/** Accuracy to a background class.
 *
 * Stepped rather than a continuous `hsl()` interpolation for two reasons: an
 * interpolated colour cannot be checked for contrast against the text sitting
 * on it, and Tailwind only ships classes it can find as complete strings in
 * the source — an interpolated `bg-${x}-500` renders as no background at all,
 * in production only, which is the trap the notice banners' tone map records.
 *
 * Both halves of every pair are named so the cell is legible in either theme.
 */
const SCALE = [
  { at: 0.85, cell: 'bg-emerald-500 dark:bg-emerald-500', text: 'text-white' },
  { at: 0.70, cell: 'bg-emerald-300 dark:bg-emerald-700', text: 'text-emerald-950 dark:text-emerald-50' },
  { at: 0.55, cell: 'bg-amber-200 dark:bg-amber-700',     text: 'text-amber-950 dark:text-amber-50' },
  { at: 0.40, cell: 'bg-orange-300 dark:bg-orange-800',   text: 'text-orange-950 dark:text-orange-50' },
  { at: 0.00, cell: 'bg-rose-400 dark:bg-rose-800',       text: 'text-white' },
]

const EMPTY = { cell: 'bg-gray-50 dark:bg-gray-800/60', text: 'text-gray-600 dark:text-gray-400' }

function shade(accuracy) {
  if (typeof accuracy !== 'number' || !Number.isFinite(accuracy)) return EMPTY
  return SCALE.find(s => accuracy >= s.at) ?? SCALE[SCALE.length - 1]
}

const asPct = v => (typeof v === 'number' && Number.isFinite(v)
  ? `${Math.round(v * 100)}%` : null)

/**
 * @param caption     describes the whole table; rendered visibly above it.
 * @param rowHeader   the corner cell's label, e.g. "Student".
 * @param rows        `[{ key, label, cells: [{accuracy, attempted} | null] }]`
 *                    — `cells` is aligned to `columns` by the server.
 * @param columns     `[{ key, label, sublabel }]`
 * @param minAttempts below this a cell is marked as too thin to trust.
 */
export default function Heatmap({ caption, rowHeader, rows, columns, minAttempts = 0 }) {
  if (!rows?.length || !columns?.length) return null

  return (
    // The wrapper scrolls, not the page: a class of thirty against a dozen
    // topics is wider than any column this sits in, and a table that widens
    // the document body breaks every other panel on the page.
    <div className="overflow-x-auto">
      <table className="w-full border-separate border-spacing-1 text-sm">
        <caption className="text-left text-xs text-gray-600 dark:text-gray-400 mb-2">
          {caption}
        </caption>
        <thead>
          <tr>
            <th scope="col" className="text-left font-bold text-gray-900 dark:text-white px-2 py-1">
              {rowHeader}
            </th>
            {columns.map(c => (
              <th key={c.key} scope="col"
                className="px-2 py-1 text-xs font-bold text-gray-900 dark:text-white whitespace-nowrap">
                {c.label}
                {c.sublabel && (
                  <span className="block font-normal text-gray-600 dark:text-gray-400">
                    {c.sublabel}
                  </span>
                )}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map(r => (
            <tr key={r.key}>
              <th scope="row"
                className="text-left font-bold text-gray-900 dark:text-white px-2 py-1 whitespace-nowrap max-w-[12rem] truncate">
                {r.label}
              </th>
              {r.cells.map((cell, i) => {
                const col = columns[i]
                const pct = asPct(cell?.accuracy)
                const thin = cell && cell.attempted < minAttempts
                const { cell: bg, text } = shade(cell?.accuracy)
                return (
                  <td key={col?.key ?? i}
                    className={`px-2 py-1.5 text-center rounded-md tabular-nums ${bg} ${text}`}
                    // The visible cell is a percentage with no context. The
                    // label is what a screen reader gets, and it carries the
                    // denominator — "3 of 4" is a different fact from "75%",
                    // and it is the one that says whether to believe it.
                    aria-label={cell
                      ? `${r.label}, ${col?.label}: ${pct}, ${cell.correct} of ${cell.attempted} correct${thin ? ', too few attempts to rely on' : ''}`
                      : `${r.label}, ${col?.label}: not attempted`}>
                    {pct ?? '–'}
                    {thin && <span aria-hidden="true" className="ml-0.5 opacity-70">*</span>}
                  </td>
                )
              })}
            </tr>
          ))}
        </tbody>
      </table>
      {minAttempts > 0 && (
        <p className="mt-2 text-xs text-gray-600 dark:text-gray-400">
          * fewer than {minAttempts} attempts. A dash means the topic has not been
          attempted, which is not the same as answering it wrongly.
        </p>
      )}
    </div>
  )
}
