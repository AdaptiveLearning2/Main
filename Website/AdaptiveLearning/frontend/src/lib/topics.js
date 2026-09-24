/**
 * The frontend's one topic list, in display order (youngest content first).
 * Mirrors `LLM_topic_decider.ALL_TOPICS`; `topics.test.js` fails if they disagree.
 */

export const TOPICS = [
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

/** A topic slug for display: every underscore becomes a space. */
export function topicLabel(topic) {
  return topic.replaceAll('_', ' ')
}
