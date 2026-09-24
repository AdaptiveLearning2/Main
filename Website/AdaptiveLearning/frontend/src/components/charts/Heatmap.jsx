/**
 * A matrix of accuracies as a real `<table>` with shaded cells; deliberately
 * not a chart wrapper, since it is already the table.
 * Cells: a number (shaded), `null` = not attempted (unshaded, never the zero
 * colour), below `minAttempts` = shown but marked thin.
 */

/**
 * Accuracy to classes. Stepped, complete class strings: Tailwind never ships
 * an interpolated class, and each pair names both themes.
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
 * @param caption     rendered visibly above the table.
 * @param rows        `[{ key, label, cells: [{accuracy, attempted} | null] }]`; server aligns `cells` to `columns`.
 * @param columns     `[{ key, label, sublabel }]`
 * @param minAttempts below this a cell is marked as too thin to trust.
 */
export default function Heatmap({ caption, rowHeader, rows, columns, minAttempts = 0 }) {
  if (!rows?.length || !columns?.length) return null

  return (
    // The wrapper scrolls, not the page.
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
                    // The label carries the denominator the visible cell lacks.
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
