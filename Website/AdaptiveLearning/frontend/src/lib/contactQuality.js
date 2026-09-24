/**
 * Electrode contact from the bridge's `hsi` / `is_good` arrays, with the same
 * thresholds as the sidecar's `_signal_quality` (HSI 1/2/4 -> 1/0.5/0, 0 skipped;
 * the worse of the two decides). Unsmoothed: the caller debounces.
 *
 * @returns {'good'|'degraded'|'poor'|null} null when neither array is reported ("not measured", not "poor")
 */
export function contactQuality(ingestion) {
  const hsi = Array.isArray(ingestion?.hsi) ? ingestion.hsi : null
  const isGood = Array.isArray(ingestion?.is_good) ? ingestion.is_good : null

  let fit = null
  if (hsi && hsi.length) {
    const rated = hsi.map(Number).filter(v => Number.isFinite(v) && v > 0)
    if (rated.length) {
      fit = rated.reduce((acc, v) => acc + (v <= 1 ? 1 : v <= 2 ? 0.5 : 0), 0) / rated.length
    }
  }

  let good = null
  if (isGood && isGood.length) {
    const values = isGood.map(Number)
    if (values.every(Number.isFinite)) {
      good = values.filter(v => v >= 1).length / values.length
    }
  }

  const parts = [fit, good].filter(v => v != null)
  if (!parts.length) return null
  const contact = Math.min(...parts)
  if (contact >= 0.8) return 'good'
  if (contact >= 0.4) return 'degraded'
  return 'poor'
}
