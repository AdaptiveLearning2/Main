/** Is this a timezone the platform can actually resolve?
 *
 * The school-year form takes the zone as free text, and a typo there is not a
 * cosmetic problem: `_retention_window()` denies on an unresolvable zone rather
 * than falling back to UTC — deliberately, because a fallback moves every term
 * boundary by hours while looking like it worked. So `America/Chigago` stops
 * recording for **every student in the deployment**, and the only sign is a
 * status line reading "the window could not be read".
 *
 * That is the right backend behaviour and the wrong place to find out. Catching
 * it in the form turns a silent platform-wide outage into a red message beside
 * the field that caused it.
 *
 * `Intl.DateTimeFormat` is the check rather than a regex or a hardcoded list:
 * it throws `RangeError` on a zone the runtime cannot resolve, which is the
 * same question Python's `ZoneInfo` asks on the other side. A regex would
 * accept `Area/Nonsense`, which is exactly the failure being prevented.
 */
export function isValidTimezone(tz) {
  if (!tz || typeof tz !== 'string') return false
  try {
    new Intl.DateTimeFormat(undefined, { timeZone: tz })
    return true
  } catch {
    return false
  }
}

/** Every zone the runtime knows, for a `<datalist>`, or `[]` where it cannot say.
 *
 * `Intl.supportedValuesOf` is recent enough to be worth guarding: an engine
 * without it should get a plain text field that still validates, not a crash.
 * The suggestions are a convenience; `isValidTimezone` is the check.
 */
export function knownTimezones() {
  try {
    return Intl.supportedValuesOf('timeZone')
  } catch {
    return []
  }
}
