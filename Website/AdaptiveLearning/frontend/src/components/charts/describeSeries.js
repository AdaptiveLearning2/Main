// The sentence half of a chart's text alternative.
//
// Split from `AccessibleChart.jsx` because a module exporting both a component
// and plain functions defeats fast refresh, which is what
// `react-refresh/only-export-components` warns about.

/** Ratios are stored 0..1 and read as percentages. */
export const asPercent = v => v * 100

/** A column spec, resolved.
 *
 * `{ key, label, unit = '', scale }` — the **same object** drives the summary
 * sentence and the `sr-only` table, which is the point. When the two were built
 * from separate literals they disagreed twice in one PR: `SessionReview` named
 * the heart series `bpm` where the rows carry `heart_rate_bpm`, so it announced
 * "not recorded" for a line visibly plotted beside it; and two pages passed raw
 * 0..1 ratios with a `%` unit, announcing a session that ranged 42–78% as
 * "Focus 0% to 1%". Neither is visible on screen, and neither test could have
 * caught them, because both surfaces were wrong in the same way at once.
 *
 * One spec cannot disagree with itself.
 */
export function readValue(row, col) {
  const raw = row?.[col.key]
  // `Number.isFinite`, not `typeof === 'number'`: `NaN` and `±Infinity` are
  // both numbers and both render as themselves, so a cell would read "NaN%" and
  // the sentence "Focus NaN% to NaN%". Nothing produces them today — the
  // mappers null out what they cannot measure — but this is the shared contract
  // every chart's text passes through, and "no reading" is the state it already
  // has a word for.
  if (!Number.isFinite(raw)) return null
  const scaled = col.scale ? col.scale(raw) : raw
  // Checked again after the scale, since a scale is caller-supplied and can
  // produce one from a perfectly good input.
  return Number.isFinite(scaled) ? scaled : null
}

/** A one-sentence summary of one series, or null when it has no readings.
 *
 * Null rather than "0 to 0": a row with no reading leaves a gap in the line
 * rather than drawing at zero, and the sentence has to make the same
 * distinction — otherwise the text alternative claims a flat week the chart
 * never showed.
 */
export function describeSeries(rows, col) {
  const values = (rows || []).map(r => readValue(r, col)).filter(v => v !== null)
  if (values.length === 0) return null
  const unit = col.unit ?? ''
  const lo = Math.round(Math.min(...values))
  const hi = Math.round(Math.max(...values))
  return lo === hi ? `${col.label} ${lo}${unit}` : `${col.label} ${lo}${unit} to ${hi}${unit}`
}

/** The whole sentence: a headline, then whichever series have readings.
 *
 * A series nobody recorded is left out rather than described as flat at zero —
 * the same distinction the gaps in the line already make.
 */
export function describeChart(headline, rows, columns) {
  return [headline, ...(columns || []).map(c => describeSeries(rows, c)).filter(Boolean)].join(' ')
}

/** The summary for a categorical breakdown — a pie, or anything named-and-counted.
 *
 * Carries its own empty case, because four of the five pie summaries were
 * written without one and would have announced `"Emotion mix: ."` for a channel
 * that recorded nothing. "Nothing recorded" is a different fact from a chart
 * that failed to load, and a caller that renders this only when it has slices
 * still gets the honest string if that guard ever moves.
 */
export function describeSlices(label, slices, noun, nameKey = 'name', valueKey = 'value') {
  if (!slices || slices.length === 0) return `${label}: nothing recorded.`
  return `${label}: ${slices.map(s => `${s[nameKey]} ${s[valueKey]} ${noun}`).join(', ')}.`
}

/** A categorical chart's whole spec — sentence, rows, and table columns.
 *
 * Spread straight into `AccessibleChart`. The point is that the noun is written
 * **once**: it names what the values count in the sentence ("neutral 40
 * samples") and it heads the table column ("Samples"). As two literals they
 * were free to drift, and one pair already had — `Analytics` built its sentence
 * from `topicData.map(d => ({ name: d.topic, value: d.count }))` while its
 * table read `topicData` with key `count`, so the same numbers reached the two
 * surfaces down two different paths for no reason. That is the shape the whole
 * one-spec rule exists to remove, surviving in the one place the rule had not
 * been applied.
 *
 * Not memoised: no page that draws a categorical chart polls. `Live` is the one
 * that re-renders on a timer and it draws a line, not a pie.
 */
// `rowLabel` has no default on purpose: all five call sites name it, and the
// one a default would have supplied ("Name") is worse than every one of them.
// An unexercised fallback on a screen-reader-only surface is a string nobody
// would ever see fail.
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
