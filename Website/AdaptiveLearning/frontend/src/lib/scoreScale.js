// The EEG score scale a rollup-backed payload was measured on (`score_scale: {min, max}`).
// A range whose ends differ straddles a change: values either side are not comparable.

export function isMixedScale(scale) {
  return !!scale && typeof scale.min === 'number' && typeof scale.max === 'number'
    && scale.min !== scale.max
}

// The widest range across several labelled rows (weeks or days), or null when
// none carries a label -- unlabelled is unknown, not scale 1.
export function combineScales(rows) {
  // Both ends must be numbers, or a half-populated row yields NaN.
  const ranges = (rows || []).map(r => r?.score_scale)
    .filter(s => s && typeof s.min === 'number' && typeof s.max === 'number')
  if (!ranges.length) return null
  return {
    min: Math.min(...ranges.map(s => s.min)),
    max: Math.max(...ranges.map(s => s.max)),
  }
}

// Scale 3 is a different calm source (`calm_source: local`, stress only), not a
// later version; it can run beside scale 2 at the same time.
export const LOCAL_CALM_SCALE = 3

export function describeScaleChange(scale) {
  if (!isMixedScale(scale)) return null
  const versionStep = scale.min < 2 && scale.max >= 2
  const sourceSplit = scale.max >= LOCAL_CALM_SCALE
  const affected = versionStep ? 'focus and stress' : 'stress'
  return { affected, versionStep, sourceSplit }
}
