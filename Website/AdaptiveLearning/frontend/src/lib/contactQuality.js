/**
 * Electrode contact, read off the bridge's own `hsi` / `is_good` arrays.
 *
 * The sidecar computes the same verdict (`signal_processing._signal_quality`,
 * `quality_basis: "contact"`) and stores it on every row, but the status
 * endpoints a page polls carry only the raw arrays -- so this is the same
 * rule, at the one place a student can act on it. Thresholds match the
 * sidecar's: HSI 1 -> 1.0, 2 -> 0.5, 4 -> 0.0 (0 is "not reported" and is
 * skipped); IS_GOOD counts usable channels; the worse of the two decides.
 *
 * Unsmoothed, unlike the sidecar, because a page samples one frame every few
 * seconds and the caller debounces instead.
 *
 * @returns {'good'|'degraded'|'poor'|null} null when neither array has been
 *   reported -- an older bridge, a headband that hasn't sent contact packets
 *   yet, or no headband at all. "Not measured" is not "poor".
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
