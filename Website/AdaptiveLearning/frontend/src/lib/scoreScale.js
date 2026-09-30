// The EEG score scale a rollup-backed payload was measured on (`score_scale: {min, max, calm_sources}`).
// Ends that differ straddle a version change; two calm sources put stress in two units.

const sourcesOf = scale => (Array.isArray(scale?.calm_sources) ? scale.calm_sources : [])

export function isMixedScale(scale) {
  if (!scale || typeof scale.min !== 'number' || typeof scale.max !== 'number') return false
  return scale.min !== scale.max || sourcesOf(scale).length > 1
}

// The widest range across several labelled rows (weeks or days), calm sources unioned,
// or null when none carries a label -- unlabelled is unknown, not scale 1.
export function combineScales(rows) {
  // Both ends must be numbers, or a half-populated row yields NaN.
  const ranges = (rows || []).map(r => r?.score_scale)
    .filter(s => s && typeof s.min === 'number' && typeof s.max === 'number')
  if (!ranges.length) return null
  return {
    min: Math.min(...ranges.map(s => s.min)),
    max: Math.max(...ranges.map(s => s.max)),
    calm_sources: [...new Set(ranges.flatMap(sourcesOf))].sort(),
  }
}

// A version step moves focus and stress; a calm-source split moves stress only.
export function describeScaleChange(scale) {
  if (!isMixedScale(scale)) return null
  const versionStep = scale.min !== scale.max
  const sourceSplit = sourcesOf(scale).length > 1
  const affected = versionStep ? 'focus and stress' : 'stress'
  return { affected, versionStep, sourceSplit }
}
