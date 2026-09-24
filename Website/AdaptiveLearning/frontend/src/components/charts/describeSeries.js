// The sentence half of a chart's text alternative. Separate from
// `AccessibleChart.jsx` so fast refresh works (`react-refresh/only-export-components`).

/** Ratios are stored 0..1 and read as percentages. */
export const asPercent = v => v * 100

/**
 * A cell's value under a column spec `{ key, label, unit = '', scale }`, or null.
 * One spec drives both the sentence and the `sr-only` table, so they cannot disagree.
 */
export function readValue(row, col) {
  const raw = row?.[col.key]
  // `Number.isFinite`, so NaN/Infinity read as "no reading", not "NaN%".
  if (!Number.isFinite(raw)) return null
  const scaled = col.scale ? col.scale(raw) : raw
  // Again after the caller-supplied scale.
  return Number.isFinite(scaled) ? scaled : null
}

/** A one-sentence summary of one series, or null (never "0 to 0") when it has no readings. */
export function describeSeries(rows, col) {
  const values = (rows || []).map(r => readValue(r, col)).filter(v => v !== null)
  if (values.length === 0) return null
  const unit = col.unit ?? ''
  const lo = Math.round(Math.min(...values))
  const hi = Math.round(Math.max(...values))
  return lo === hi ? `${col.label} ${lo}${unit}` : `${col.label} ${lo}${unit} to ${hi}${unit}`
}

/** The whole sentence: a headline, then whichever series have readings. */
export function describeChart(headline, rows, columns) {
  return [headline, ...(columns || []).map(c => describeSeries(rows, c)).filter(Boolean)].join(' ')
}

/** The summary for a categorical breakdown, with its own "nothing recorded" case. */
export function describeSlices(label, slices, noun, nameKey = 'name', valueKey = 'value') {
  if (!slices || slices.length === 0) return `${label}: nothing recorded.`
  return `${label}: ${slices.map(s => `${s[nameKey]} ${s[valueKey]} ${noun}`).join(', ')}.`
}

/**
 * A categorical chart's whole spec (sentence, rows, columns), spread into `AccessibleChart`.
 * The noun is written once: it counts in the sentence and heads the table column.
 * `rowLabel` has no default on purpose.
 */
export function sliceSpec(label, rows, noun,
                          { nameKey = 'name', valueKey = 'value', rowLabel } = {}) {
  return {
    summary: describeSlices(label, rows, noun, nameKey, valueKey),
    rows,
    rowKey: nameKey,
    rowLabel,
    columns: [{ key: valueKey, label: noun.charAt(0).toUpperCase() + noun.slice(1) }],
  }
}
