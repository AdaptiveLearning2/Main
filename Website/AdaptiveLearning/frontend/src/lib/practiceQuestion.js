/** Practice.jsx's old static-bank rows used `{options, correct_answer}`; the
 * AI generators (LLM_*_generation.py, via question_generation) return
 * `answer_options` -- the same shape Adaptive.jsx reads. This reconciles the
 * two into one shape a question card can render regardless of which
 * generated it, rather than each page re-deriving its own reveal styling
 * against a slightly different field name.
 *
 * Pulled out of `components/practice/QuestionCard.jsx` (not co-located with
 * it) so that file exports only the component -- `react-refresh/only-export-
 * components` flags a component file that also exports plain functions.
 *
 * Returns null for a question that can't actually be shown or scored --
 * missing/empty options, or a missing correct answer -- so a caller can drop
 * it the same way the old Practice.jsx's `isAnswerable` did.
 */
export function normalizeQuestion(raw) {
  if (!raw) return null
  const options = raw.options || raw.answer_options
  if (!Array.isArray(options) || options.length === 0) return null
  // Not `!raw.correct_answer` -- a correct answer of 0 or "" is valid and falsy.
  if (raw.correct_answer === null || raw.correct_answer === undefined) return null
  return {
    id: raw.id,
    text: raw.question_text,
    topic: raw.question_topic || raw.subject,
    difficulty: raw.difficulty,
    options,
    correctAnswer: raw.correct_answer,
  }
}

/** A value as it should be compared for correctness -- arrays (e.g. an
 * ordering answer) join into one string so `[3, 1, 2]` and "3, 1, 2" compare
 * equal regardless of which shape a particular option arrived in.
 */
export function normalizeValue(val) {
  if (Array.isArray(val)) return val.join(', ')
  return String(val).trim()
}
