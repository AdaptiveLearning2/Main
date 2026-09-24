/** A "month day" date for a sentence, or null (never "Invalid Date"). */
export function fmtDate(s) {
  if (!s) return null
  const d = new Date(s)
  return Number.isNaN(d.getTime())
    ? null
    : d.toLocaleDateString([], { month: 'long', day: 'numeric' })
}
