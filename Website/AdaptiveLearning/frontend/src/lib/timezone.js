/** Is this a timezone the backend will accept?
 *
 * The backend validates with Python's `ZoneInfo`, which is stricter than
 * `Intl.DateTimeFormat`: it rejects UTC-offset strings (`+05:30`) and
 * lowercase names (`america/chicago`) that `Intl` happily accepts. This
 * check mirrors those two rules so a bad value is caught before the round
 * trip, instead of the form claiming success and the save then failing.
 */

// Offset strings start with a sign (`Etc/GMT-5` doesn't, and is a valid
// name, so this must anchor at the start rather than search anywhere).
const OFFSET = /^[+-]/

export function isValidTimezone(tz) {
  if (!tz || typeof tz !== 'string') return false
  if (OFFSET.test(tz)) return false
  try {
    const canonical = new Intl.DateTimeFormat(undefined, { timeZone: tz })
      .resolvedOptions().timeZone
    // Same letters, different case -> a typo, which ZoneInfo rejects.
    // Genuinely different letters -> an alias (e.g. GMT -> UTC), which
    // ZoneInfo accepts, so don't reject those.
    if (canonical && canonical !== tz
        && canonical.toLowerCase() === tz.toLowerCase()) return false
    return true
  } catch {
    return false
  }
}

/** Every zone the runtime knows, for a `<datalist>`, or `[]` where it cannot say.
 *
 * Suggestions only, not the validity check — the list omits `UTC`, which is
 * a valid zone and this form's own default, so using it as a check would
 * mark a freshly loaded form invalid.
 */
export function knownTimezones() {
  try {
    return Intl.supportedValuesOf('timeZone')
  } catch {
    return []
  }
}
