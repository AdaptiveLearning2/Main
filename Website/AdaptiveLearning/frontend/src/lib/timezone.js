/**
 * Whether the backend's `ZoneInfo` will accept this timezone: stricter than
 * `Intl`, it rejects offsets (`+05:30`) and wrong case (`america/chicago`).
 */

// Anchored: `Etc/GMT-5` is a valid name.
const OFFSET = /^[+-]/

export function isValidTimezone(tz) {
  if (!tz || typeof tz !== 'string') return false
  if (OFFSET.test(tz)) return false
  try {
    const canonical = new Intl.DateTimeFormat(undefined, { timeZone: tz })
      .resolvedOptions().timeZone
    // Case-only difference: rejected. A real alias (GMT -> UTC): accepted.
    if (canonical && canonical !== tz
        && canonical.toLowerCase() === tz.toLowerCase()) return false
    return true
  } catch {
    return false
  }
}

/** Zones for a `<datalist>`, or `[]`. Suggestions only: the list omits `UTC`. */
export function knownTimezones() {
  try {
    return Intl.supportedValuesOf('timeZone')
  } catch {
    return []
  }
}
