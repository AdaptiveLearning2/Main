/**
 * Per-series `switch` toggles above a signal chart. Any combination, including
 * none, may be on; the chart explains an empty view.
 * The swatch is the series' own `colour`, applied inline (never an interpolated
 * Tailwind class). Renders nothing below two series.
 */
export default function SeriesFilter({ series, hidden, onToggle, label = 'Measurements shown' }) {
  if (!series || series.length < 2) return null

  return (
    <div role="group" aria-label={label} className="flex flex-wrap gap-2 mb-3">
      {series.map((s) => {
        const on = !hidden.has(s.key)
        return (
          <button
            key={s.key}
            type="button"
            role="switch"
            aria-checked={on}
            onClick={() => onToggle(s.key)}
            // `dark:text-gray-100`, not `dark:text-white`: contrast.test.js only resolves `gray-N`.
            className={`inline-flex items-center gap-2 px-3 py-2.5 min-h-[44px] rounded-lg border text-xs font-bold transition ${
              on
                ? 'border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-900 text-gray-900 dark:text-gray-100'
                : 'border-gray-200 dark:border-gray-700 bg-slate-50 dark:bg-gray-800 text-gray-600 dark:text-gray-400'
            }`}
          >
            <span
              aria-hidden="true"
              className="w-2.5 h-2.5 rounded-full shrink-0"
              // Hollow when off. `s.colour` (British spelling), as every call site writes it.
              style={on
                ? { backgroundColor: s.colour }
                : { boxShadow: `inset 0 0 0 2px ${s.colour}`, opacity: 0.5 }}
            />
            {s.label}
          </button>
        )
      })}
    </div>
  )
}
