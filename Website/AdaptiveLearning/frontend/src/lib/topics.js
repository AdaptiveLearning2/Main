/**
 * The frontend's one topic list, in display order (youngest content first).
 * Mirrors `LLM_topic_decider.ALL_TOPICS`; `topics.test.js` fails if they disagree.
 */

export const TOPICS = [
  'counting',
  'comparing_numbers',
  'add_and_subtract',
  'teen_numbers',
  'shapes',
  'ordering',
  'missing_number',
  'patterns',
  'graphs',
  'shape_fractions',
  'rationals',
  'expressions',
  'algebra',
  'geometry',
  'angle_relationships',
  'mean',
  'median',
  'mode',
  'probability',
  'quadratics',
  'functions',
  'spread',
]

/** One emoji per topic; every topic needs one (a missing icon renders silently empty). */
export const TOPIC_ICONS = {
  counting: '🧮',
  comparing_numbers: '⚖️',
  add_and_subtract: '➕',
  teen_numbers: '🔟',
  shapes: '🔺',
  ordering: '🔢',
  missing_number: '❓',
  patterns: '📶',
  graphs: '📊',
  shape_fractions: '🥧',
  rationals: '➗',
  expressions: '📐',
  algebra: '🔣',
  geometry: '📏',
  angle_relationships: '📐',
  mean: '〰️',
  median: '📊',
  mode: '🔁',
  probability: '🎲',
  quadratics: '📈',
  functions: 'ƒ',
  spread: '📉',
}

/**
 * The topics a page lists for one student: the grade's, plus any already attempted, in `TOPICS` order.
 * `allowed` null (not known) lists every topic rather than hide one the student may be served.
 */
export function topicsToShow(allowed, attempted = []) {
  if (!allowed) return TOPICS
  return TOPICS.filter(t => allowed.includes(t) || attempted.includes(t))
}

/** A topic slug for display: every underscore becomes a space. */
export function topicLabel(topic) {
  return topic.replaceAll('_', ' ')
}
