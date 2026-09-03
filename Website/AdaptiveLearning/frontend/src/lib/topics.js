/**
 * The topic list, once.
 *
 * There were six copies of this across the app and they disagreed. The two
 * teacher surfaces had never been updated past the original ten, so
 * `Analytics.jsx` counted questions on the newer topics in nothing at all --
 * its chart drops empty bars, so they vanished with no hint that anything was
 * missing -- and `Questions.jsx` offered no way to filter the bank to them.
 * Both bite hardest for grades 1-3, whose topics those are.
 *
 * That was the third time in three changes that adding a topic left a
 * frontend list behind. The list is not the sort of thing anyone remembers to
 * grep for, so it stops being a thing to remember.
 *
 * **This still duplicates `LLM_topic_decider.ALL_TOPICS`**, because a React
 * bundle cannot import Python. What closes that is `topics.test.js`, which
 * parses the backend's list and fails if the two disagree -- so the copy is
 * checked rather than trusted, and a topic added on one side of the stack
 * cannot quietly go missing on the other.
 *
 * Order is display order, youngest content first, and is this file's own
 * business -- the backend's list is a set as far as the check is concerned.
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

/**
 * One emoji per topic. Every topic needs one: a tile whose icon is `undefined`
 * renders an empty slot rather than an error, which is how two topics shipped
 * iconless on the student dashboard and nothing caught it.
 */
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

/** A topic slug as a reader should see it: `angle_relationships` -> `angle
 * relationships`. Every underscore, not the first -- `String.replace` with a
 * string pattern replaces one, which was fine while every slug had at most
 * one and is a trap for the next slug that does not. */
export function topicLabel(topic) {
  return topic.replaceAll('_', ' ')
}
