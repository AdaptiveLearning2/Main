/** Is this a timezone the *backend* will accept?
 *
 * The school-year form takes the zone as free text. The backend
 * (`admin_set_retention_window`) validates it with Python's `ZoneInfo` and
 * returns a 422 before persisting anything, so a bad value is never stored and
 * recording never stops — this check exists to say so *before* the round trip,
 * not to stand between a typo and an outage.
 *
 * That makes agreeing with `ZoneInfo` the whole job. A check that accepts what
 * the backend then rejects is worse than no check: it tells an admin their
 * value is fine and the save fails anyway, which reads as a broken form rather
 * than a bad zone.
 *
 * `Intl.DateTimeFormat` alone does **not** agree, in two measured ways
 * (Node 22 / V8 vs CPython 3.12):
 *
 * | value | `Intl` | `ZoneInfo` |
 * | --- | --- | --- |
 * | `+00:00`, `+05:30` | accepts | rejects |
 * | `america/chicago`  | accepts | rejects |
 * | `America/Chicago`  | accepts | accepts |
 *
 * So: reject UTC-offset strings outright, and reject a name that differs from
 * its canonical form only by case. `ZoneInfo`'s lookup is a case-sensitive path
 * lookup in the tz database, and the raw string is what gets saved.
 *
 * The case test is deliberately **not** a plain round-trip equality. `Intl`
 * canonicalises legacy aliases — `US/Central` → `America/Chicago`, `GMT` →
 * `UTC` — and `ZoneInfo` accepts both of those, so rejecting anything that does
 * not round-trip exactly would block valid saves. Comparing only when the two
 * differ in case alone separates "you typed it in the wrong case" from "that is
 * an alias".
 */

// Every offset form starts with a sign. `Etc/GMT-5` does not, and both sides
// accept it, so this must anchor rather than search for a sign anywhere.
const OFFSET = /^[+-]/

export function isValidTimezone(tz) {
  if (!tz || typeof tz !== 'string') return false
  if (OFFSET.test(tz)) return false
  try {
    const canonical = new Intl.DateTimeFormat(undefined, { timeZone: tz })
      .resolvedOptions().timeZone
    // Same letters, different case -> a case error, which ZoneInfo rejects.
    // Genuinely different -> an alias, which ZoneInfo accepts.
    if (canonical && canonical !== tz
        && canonical.toLowerCase() === tz.toLowerCase()) return false
    return true
  } catch {
    return false
  }
}

/** Every zone the runtime knows, for a `<datalist>`, or `[]` where it cannot say.
 *
 * Suggestions only — `isValidTimezone` is the check. Not used as the check
 * itself because the list is canonical names *and nothing else*: it omits `UTC`
 * (measured: 417 entries, `UTC` not among them), which `ZoneInfo` accepts and
 * which is this form's own default value. Validating against it would mark a
 * freshly loaded form invalid and disable its Save button.
 */
export function knownTimezones() {
  try {
    return Intl.supportedValuesOf('timeZone')
  } catch {
    return []
  }
}
