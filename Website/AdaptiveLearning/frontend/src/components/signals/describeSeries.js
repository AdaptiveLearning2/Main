// Split out of `ChartFallback.jsx` because a module that exports both a
// component and a plain function defeats fast refresh, which is what
// `react-refresh/only-export-components` is warning about. The description
// and the table are two halves of one idea; only the file boundary moves.

/** A one-sentence summary of one series, or null when it has no readings.
 *
 * Null rather than "0 to 0": a day with no reading leaves a gap in the line
 * rather than drawing at zero, and the sentence has to make the same
 * distinction the chart does — otherwise the text alternative claims a quiet
 * week the chart never showed.
 */
export function describeSeries(rows, key, label, unit = '%') {
  const values = (rows || []).map(r => r?.[key]).filter(v => typeof v === 'number')
  if (values.length === 0) return null
  const lo = Math.min(...values)
  const hi = Math.max(...values)
  return lo === hi
    ? `${label} ${Math.round(lo)}${unit}`
    : `${label} ${Math.round(lo)}${unit} to ${Math.round(hi)}${unit}`
}
