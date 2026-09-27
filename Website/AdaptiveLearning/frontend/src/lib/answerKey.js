/** A stored question's options and which one is right; `questions.correct_answer` is text, not an index. */

/** `options` (unschema'd jsonb) as strings: an array or object, else `[]`. A list option joins. */
export function optionList(q) {
  const raw = q?.options
  if (Array.isArray(raw)) return raw.map(String)
  if (raw && typeof raw === 'object') return Object.values(raw).map(String)
  return []
}

/**
 * Index of the correct option, or -1. Matched by value (trimmed, case-insensitive), first match
 * only, so a duplicate distractor isn't also marked. A list answer is stored as JSON text
 * (`["2", "5"]`) and compared joined; a bounds-checked numeric fallback covers a stored index.
 */
export function correctIndex(q, opts) {
  const want = q?.correct_answer
  if (want === null || want === undefined) return -1
  let a = String(want).trim()
  if (a.startsWith('[')) {
    try {
      const parsed = JSON.parse(a)
      if (Array.isArray(parsed)) a = parsed.map(String).join(',')
    } catch {
      // Not JSON: compared as the text it is.
    }
  }
  a = a.toLowerCase()
  const byValue = opts.findIndex(o => String(o).trim().toLowerCase() === a)
  if (byValue !== -1) return byValue
  if (/^\d+$/.test(a)) {
    const n = Number(a)
    if (n >= 0 && n < opts.length) return n
  }
  return -1
}
