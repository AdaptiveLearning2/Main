/** How a live surface describes the newest headband row: its age, and whether it was usable. */

/** Must match the backend's `_LIVE_WINDOW_SEC`. */
export const STALE_AFTER_S = 90

/** "12s ago" / "3m ago" for a reading's age in ms, or null for no reading. */
export function formatAge(ms) {
  if (ms == null || !Number.isFinite(ms)) return null
  const s = Math.max(0, Math.round(ms / 1000))
  if (s < 60) return `${s}s ago`
  return `${Math.floor(s / 60)}m ago`
}

/**
 * Whether the newest cognitive row was recorded with poor electrode contact
 * (a row with every measurement nulled). A heuristic "poor" is not counted.
 */
export function eegWeak(cog) {
  if (!cog) return false
  const raw = cog.raw || {}
  if (raw.signal_quality === 'poor' && raw.quality_basis === 'contact') return true
  return cog.focus == null && cog.engagement == null && cog.stress == null
}
