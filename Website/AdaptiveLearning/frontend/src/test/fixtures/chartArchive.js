/**
 * `GET /api/signals/session/{id}/charts`, in each state `archivedChart()` reads.
 *
 * Three of the five states live in this payload; the other two are the page's
 * own (`pending` while the request is outstanding, `failed` when it rejects),
 * so a test reaches those through the mock's timing rather than through a
 * fixture.
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

/** Archived, but the named objects could not be signed or read.
 *
 *  A fault, not an absence -- and it is per chart, so the mixed case (one
 *  readable, one not, in a section that draws both) is ordinary rather than a
 *  corner case. That mix is what `anyUnavailable` exists for. */
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

/** The archive never ran -- a session closed before chart archiving existed, or
 *  one with no signal samples to draw. Distinct from every failure above. */
export const ARCHIVE_UNARCHIVED = { archived: false, charts: {}, unavailable: [] }
