/**
 * `GET /api/signals/session/{id}/charts`, in each state `archivedChart()` reads.
 *
 * Covers three of five states; `pending` and `failed` are the page's own
 * states and come from the mock's timing instead, not a fixture here.
 */

export const CHART_NAMES = ['cognitive_timeline', 'heart_rate', 'emotion_pie', 'stress_pie']

const url = (name) => `https://storage.example/signed/${name}.svg?token=t`

/** Archived, every chart readable. */
export function buildChartArchive(overrides = {}) {
  return {
    archived: true,
    charts: Object.fromEntries(CHART_NAMES.map(n => [n, url(n)])),
    unavailable: [],
    ...overrides,
  }
}

/** Archived, but the named objects could not be signed or read — a fault,
 *  not an absence. Per-chart, so a mix of readable and unreadable charts in
 *  one section is a normal case, not an edge case. */
export function withUnavailable(payload, names) {
  const list = [].concat(names)
  return {
    ...payload,
    charts: Object.fromEntries(
      Object.entries(payload.charts).filter(([n]) => !list.includes(n))),
    unavailable: [...payload.unavailable, ...list],
  }
}

/** Archived, read fine, and this channel genuinely drew nothing. */
export function withEmpty(payload, names) {
  const list = [].concat(names)
  return {
    ...payload,
    charts: Object.fromEntries(
      Object.entries(payload.charts).filter(([n]) => !list.includes(n))),
  }
}

/** The archive never ran — an old session, or one with no samples to draw.
 *  Distinct from the failure states above. */
export const ARCHIVE_UNARCHIVED = { archived: false, charts: {}, unavailable: [] }
