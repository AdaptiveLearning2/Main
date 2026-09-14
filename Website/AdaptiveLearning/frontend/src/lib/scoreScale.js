// The EEG score scale a rollup-backed payload was measured on.
//
// The sidecar's population bounds -- the scale every focus and stress value
// is measured on -- were widened in the accuracy work, which re-anchors every
// stored value. Each per-sample row records `raw.score_scale`, the daily
// rollup records the range seen each day, and every rollup-backed payload
// carries `score_scale: {min, max}` for its window. A range whose ends differ
// straddles the change: the numbers on either side are not comparable.
//
// Plain functions, in their own module rather than beside the component, so
// the component file exports only a component (react-refresh).

export function isMixedScale(scale) {
  return !!scale && typeof scale.min === 'number' && typeof scale.max === 'number'
    && scale.min !== scale.max
}

// The widest range across several labelled rows (weeks or days), or null when
// none carries a label -- unlabelled is unknown, not scale 1.
export function combineScales(rows) {
  const ranges = (rows || []).map(r => r?.score_scale).filter(s => s && typeof s.min === 'number')
  if (!ranges.length) return null
  return {
    min: Math.min(...ranges.map(s => s.min)),
    max: Math.max(...ranges.map(s => s.max)),
  }
}
