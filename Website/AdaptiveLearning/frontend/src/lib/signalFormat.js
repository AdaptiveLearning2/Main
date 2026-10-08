// Pure tile formatting for signal panels. Its own module so a page that only formats
// figures does not import SignalPanel, and with it the whole chart library.

// Why a channel has no value. Never "no data" for something never recorded.
const CHANNEL_STATE = {
  revoked: since => (since ? `Off since ${since}` : 'Not recorded'),
  // Consent read failed: we can't claim the student turned it off.
  unknown: () => 'Unavailable',
  // Samples arrived, none usable.
  calibrating: () => 'Calibrating',
  // A parent erased its past; consent may be on, so this is not "No sensor".
  erased: on => (on ? `Erased ${on}` : 'Erased'),
  noSensor: () => 'No sensor',
}

// Short date, or null (never "Invalid Date") when there is none.
function shortDate(iso) {
  if (!iso) return null
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? null : d.toLocaleDateString(undefined,
    { day: 'numeric', month: 'short' })
}

/** Tile text for a channel with no value: consent unreadable, revoked, calibrating, erased, or no sensor. */
export function offLabel({ on, revokedAt, consentRetrieved, samples, erasedAt = null }) {
  if (consentRetrieved === false) return CHANNEL_STATE.unknown()
  if (!on) return CHANNEL_STATE.revoked(shortDate(revokedAt))
  if (samples > 0) return CHANNEL_STATE.calibrating()
  // Samples since the erasure win above; with none, the empty past is the erasure, not a missing sensor.
  return erasedAt ? CHANNEL_STATE.erased(shortDate(erasedAt)) : CHANNEL_STATE.noSensor()
}

/**
 * A rendered value, or the reason there isn't one.
 * Every tile goes through here, so `pct()`'s 'N/A' never reaches the screen.
 */
export function valueOrReason(value, reason) {
  return (value && value !== 'N/A') ? value : offLabel(reason)
}

// Signals arrive as 0..1 ratios. A finite number, or null: Number('') is 0,
// so a blank must not become a confident "0%".
export function ratio(value) {
  if (value === null || value === undefined) return null
  if (typeof value === 'string' && value.trim() === '') return null
  const n = Number(value)
  return Number.isFinite(n) ? n : null
}

export function pct(value) {
  const n = ratio(value)
  return n === null ? 'N/A' : `${Math.round(n * 100)}%`
}

// `face_included` is the legacy alias; absent on both reads as on.
export function emotionOn(report) {
  if (report?.emotion_included !== undefined) return report.emotion_included !== false
  return report?.face_included !== false
}
