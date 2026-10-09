// Payloads of /api/admin/funnel, /api/admin/consent-ops and /api/admin/signal-quality, as main.py builds them.

export const FUNNEL_PATH = '/api/admin/funnel'
export const CONSENT_OPS_PATH = '/api/admin/consent-ops?weeks=8'
export const QUALITY_PATH = '/api/admin/signal-quality?days=14'

export function buildFunnel(over = {}) {
  return {
    retrieved: true,
    funnel: {
      students: { signed_up: 30, joined_class: 28, parent_linked: 20, consenting_now: 18,
                  had_a_lesson: 25, had_a_headband_lesson: 12 },
      teachers: { signed_up: 2, made_a_class: 2, class_has_a_student: 1 },
      parents: { signed_up: 22, linked_a_child: 20 },
    },
    ...over,
  }
}

export function buildConsentOps(over = {}) {
  return {
    retrieved: true,
    weeks: 8,
    since: '2026-08-17T05:00:00+00:00',
    timezone: 'America/Chicago',
    changes: [
      { week: '2026-09-28', channel: 'camera', kind: 'parent_enabled', n: 3 },
      { week: '2026-10-05', channel: 'eeg', kind: 'withdrawn', n: 1 },
      { week: '2026-10-05', channel: 'headband_optical', kind: 'erased', n: 2 },
    ],
    enablements_recorded_from: '2026-09-28',
    ...over,
  }
}

export const heartDay = (over = {}) => ({
  day: '2026-10-08', source: 'muse_optics', withheld: false, students: 8, rows: 900, synthetic_rows: 450,
  sqi_deciles: { 7: 100, 9: 500, 10: 200 }, sessions: 8, never_calibrated: 1,
  median_seconds_to_calibrate: 95.0, p90_seconds_to_calibrate: 140.5, ...over,
})

export function buildSignalQuality(over = {}) {
  return {
    retrieved: true,
    days: 14,
    since: '2026-09-25',
    timezone: 'America/Chicago',
    min_students: 5,
    eeg: [
      { day: '2026-10-07', withheld: true },
      { day: '2026-10-08', withheld: false, students: 9, samples: 5400, trusted: 4590 },
    ],
    heart: [heartDay(), { day: '2026-10-08', source: 'rppg', withheld: true }],
    ...over,
  }
}
