/** A date for a sentence, or null when there isn't one to show.
 *
 * Null rather than a placeholder, so a caller renders nothing at all instead
 * of "on Invalid Date" -- the use sites read
 * `{fmtDate(x) && <> on {fmtDate(x)}</>}`.
 *
 * Its own module because `react-refresh/only-export-components` forbids a
 * component file exporting anything else, and it was copied verbatim into two
 * of the three consent banners.
 *
 * Note for whoever needs a date next: `toLocaleDateString` is called in nine
 * other files with their own formats. This is not yet the one place dates are
 * formatted, and pretending otherwise by importing it for a different format
 * would be worse than another local helper.
 */
export function fmtDate(s) {
  if (!s) return null
  const d = new Date(s)
  return Number.isNaN(d.getTime())
    ? null
    : d.toLocaleDateString([], { month: 'long', day: 'numeric' })
}
