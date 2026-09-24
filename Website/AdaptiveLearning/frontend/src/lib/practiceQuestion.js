/**
 * One question-card shape from either `options` or `answer_options` rows.
 * Null for a question that cannot be shown or scored (no options, no correct answer).
 */
export function normalizeQuestion(raw) {
  if (!raw) return null
  const options = raw.options || raw.answer_options
  if (!Array.isArray(options) || options.length === 0) return null
  // Not `!raw.correct_answer`: 0 and "" are valid answers.
  if (raw.correct_answer === null || raw.correct_answer === undefined) return null
  return {
    id: raw.id,
    text: raw.question_text,
    topic: raw.question_topic || raw.subject,
    difficulty: raw.difficulty,
    options,
    correctAnswer: raw.correct_answer,
    // A fixed object: any key not named here is lost downstream.
    figure: raw.figure ?? null,
    ccssStandard: raw.ccss_standard ?? null,
  }
}

/** A value for correctness comparison; arrays join so `[3, 1, 2]` equals "3, 1, 2". */
export function normalizeValue(val) {
  if (Array.isArray(val)) return val.join(', ')
  return String(val).trim()
}
