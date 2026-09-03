/**
 * How a live surface describes the newest headband row: its age, and whether
 * it was usable. In `lib/` rather than beside the card so a second live
 * surface reads the same rule, and because a page file that exports helpers
 * breaks fast refresh.
 */

/** Matches the backend's `_LIVE_WINDOW_SEC`: past this a reading is the last
 *  thing the session said, not what it is saying now. The two surfaces must
 *  agree on the line, or a card reads "live" here and "stale" one page over. */
export const STALE_AFTER_S = 90

/** "12s ago" / "3m ago" for a reading's age in ms, or null for no reading. */
export function formatAge(ms) {
  if (ms == null || !Number.isFinite(ms)) return null
  const s = Math.max(0, Math.round(ms / 1000))
  if (s < 60) return `${s}s ago`
  return `${Math.floor(s / 60)}m ago`
}

/**
 * Whether the newest cognitive row was recorded with poor electrode contact.
 *
 * `map_eeg_to_cognitive` keeps the row and nulls every measurement on poor
 * contact, so a present row with no focus *is* that verdict; `raw` carries
 * the reason for a reader that wants it. A heuristic "poor" is deliberately
 * not counted: the legacy rule says poor for any focused student.
 */
export function eegWeak(cog) {
  if (!cog) return false
  const raw = cog.raw || {}
  if (raw.signal_quality === 'poor' && raw.quality_basis === 'contact') return true
  return cog.focus == null && cog.engagement == null && cog.stress == null
}
