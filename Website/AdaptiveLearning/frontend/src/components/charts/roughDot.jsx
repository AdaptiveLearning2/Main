/** A Recharts `dot` renderer for series `s`: hollow, in the chart panel's colour, where `s.rough(row)` holds. */
export function roughDot(s) {
  return function RoughDot({ cx, cy, payload, index }) {
    if (cx == null || cy == null) return null
    const rough = s.rough(payload)
    return (
      <circle key={index} cx={cx} cy={cy} r={3} stroke={s.colour} strokeWidth={rough ? 1.5 : 0}
              fill={rough ? undefined : s.colour} data-rough={rough || undefined}
              className={rough ? 'fill-slate-50 dark:fill-gray-800' : undefined} />
    )
  }
}
