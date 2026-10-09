// The one place a signal measure gets its name and explanation; tiles, charts and tips read it.
// EEG stress is shown as calm, the right way up, and is never named "Stress" (CLAUDE.md).

export const MEASURES = {
  focus: {
    name: 'Focus',
    what: "How settled the headband's brain readings were during questions.",
    higher: 'More focused',
    unit: '%',
    ratio: true,
  },
  calm: {
    name: 'Calm',
    what: "The headband's relaxation reading.",
    higher: 'Calmer',
    unit: '%',
    ratio: true,
  },
  heart_rate_bpm: {
    name: 'Heart rate',
    what: 'Beats per minute, from the heart sensor.',
    higher: 'A faster heartbeat',
    unit: ' bpm',
    ratio: false,
  },
  rmssd_ms: {
    name: 'Heart-rate variability',
    what: 'Small changes in the time between heartbeats, from the headband.',
    higher: 'Usually more relaxed',
    unit: ' ms',
    ratio: false,
  },
  body_arousal: {
    name: 'Body arousal (heart rate)',
    what: "Share of the headband's usable heart readings at least 10 beats a minute above the "
      + 'rate it measured at the start of each lesson.',
    higher: "More of the lesson well above its starting heart rate",
    caveat: 'It rises with excitement, effort and movement as well as with stress.',
    unit: '%',
    ratio: true,
  },
}

// A compared measure's verdict, in neutral words: never "good" or "bad".
export const USUAL_WORDS = {
  about_usual: 'About usual',
  higher: 'Higher than usual',
  lower: 'Lower than usual',
}

// A measure that was not compared, and why; null says nothing (no figure, or not asked for).
const NOT_COMPARED = {
  not_enough_history: 'Not enough history yet',
  too_little_this_period: 'Too few readings to compare',
  pending: "Compared once this week's lessons finish",
  not_retrieved: "Couldn't compare with earlier weeks",
  no_current: null,
  not_requested: null,
}

const NOT_COMPARABLE = {
  mixed_scale: "Not comparable: the headband's scoring changed",
  scale_unknown: 'Not comparable: scoring not recorded',
  sensor_changed: 'Not comparable: more than one heart sensor',
}

// Why body arousal has no figure, by its backend `state`; `measured` has one.
export const AROUSAL_REASONS = {
  calibrating: 'Calibrating',
  pending: 'Lesson in progress',
  unusable: 'Readings too unsteady',
  camera_only: 'Headband only',
  none: 'No headband readings',
  unknown: "Couldn't check",
  not_retrieved: "Couldn't load",
}

/** A measure's value as the page prints it (`Math.round`, as the backend's verdict does), or null. */
export function formatMeasure(key, value) {
  const m = MEASURES[key]
  if (!m || value === null || value === undefined || !Number.isFinite(Number(value))) return null
  const n = Number(value)
  return `${Math.round(m.ratio ? n * 100 : n)}${m.unit}`
}

/** The verdict words for a `usual` measure entry, or the reason it was not compared; null for none. */
export function usualWords(entry) {
  if (!entry?.status) return null
  if (entry.status === 'compared') return USUAL_WORDS[entry.verdict] ?? null
  if (entry.status === 'not_comparable') return NOT_COMPARABLE[entry.reason] ?? 'Not comparable'
  return NOT_COMPARED[entry.status] ?? null
}

/** "Usual 55–65%" for a compared measure, else null. */
export function usualRange(key, entry) {
  if (entry?.status !== 'compared') return null
  const low = formatMeasure(key, entry.low)
  const high = formatMeasure(key, entry.high)
  if (!low || !high) return null
  const unit = MEASURES[key].unit
  return `Usual ${low.slice(0, -unit.length)}–${high}`
}
