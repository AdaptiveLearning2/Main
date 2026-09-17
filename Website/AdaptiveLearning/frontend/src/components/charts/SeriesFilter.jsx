/**
 * The measurement toggles above a signal chart: focus, stress, heart rate,
 * RMSSD — whichever of them the chart below can draw.
 *
 * Each toggle is a `switch`, matching `HideSensorDataToggle`, so the control
 * announces its own state rather than relying on colour. Any combination may
 * be on, including none — the chart says so in words rather than this control
 * refusing the last click, because a toggle that silently does nothing is
 * harder to understand than an empty chart that explains itself and offers a
 * way back.
 *
 * **The colour swatch is the series' own colour, passed in and applied
 * inline.** Not a Tailwind class: those have to be complete strings in the
 * source for the build to ship them, so a palette that lives as hex in the
 * chart cannot become `bg-${…}` here — and CLAUDE.md records that failing in
 * production only, where nothing renders and no test can see it. Taking the
 * value the line is drawn with also means the swatch cannot drift from the
 * line it names.
 *
 * Renders nothing below two series: a filter over one measurement offers a
 * choice between that measurement and an empty chart.
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
            // min-h-[44px] for the touch target, as on the sensor switch.
            // Both branches carry a dark variant and both clear AA on the
            // surfaces this sits on; a grey with no `dark:` companion renders
            // its bare colour in dark mode too.
            // `dark:text-gray-100`, not `dark:text-white`: `contrast.test.js`
            // resolves the dark foreground from a `dark:text-gray-N` token and
            // otherwise falls back to the bare one, which is the right
            // modelling — an element with no dark companion really does render
            // its light colour in dark mode. `dark:text-white` is a companion
            // it cannot see, so this branch scored gray-900 on gray-900 and
            // failed the pairing check. Naming a grey keeps the check able to
            // do its arithmetic.
            className={`inline-flex items-center gap-2 px-3 py-2.5 min-h-[44px] rounded-lg border text-xs font-bold transition ${
              on
                ? 'border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-900 text-gray-900 dark:text-gray-100'
                : 'border-gray-200 dark:border-gray-700 bg-slate-50 dark:bg-gray-800 text-gray-600 dark:text-gray-400'
            }`}
          >
            <span
              aria-hidden="true"
              className="w-2.5 h-2.5 rounded-full shrink-0"
              // Hollow when off, so the chip still reads as "this one is not
              // being drawn" without colour being the only signal — the
              // `aria-checked` above is what actually carries it.
              style={on
                ? { backgroundColor: s.color }
                : { boxShadow: `inset 0 0 0 2px ${s.color}`, opacity: 0.5 }}
            />
            {s.label}
          </button>
        )
      })}
    </div>
  )
}
