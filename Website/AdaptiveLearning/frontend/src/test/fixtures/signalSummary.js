/** Signal payload builders, and the four states a tile can be in. */

/** Well above any row cap, so a count from rows can't accidentally match. */
export const WEEK_OF_SAMPLES = 51840

/** Per-student summary, as `/api/students/{id}/signal-summary` and `my_children` return it. */
export function buildSignalSummary(overrides = {}) {
  return {
    focus: 0.7,
    stress: 0.4,
    engagement: 0.6,
    face_attention: 0.9,
    sessions: 3,
    cognitive_samples: WEEK_OF_SAMPLES,
    face_samples: WEEK_OF_SAMPLES,
    face_included: true,
    dominant_emotion: 'happy',
    retrieved: true,
    ...overrides,
  }
}

/** `usual.measures[key]` as `_personal_baseline` sends it; `compared` unless overridden. */
export function buildUsualMeasure(overrides = {}) {
  return {
    status: 'compared', verdict: 'about_usual', reason: null, current: 0.6,
    median: 0.6, p25: 0.55, p75: 0.65, low: 0.55, high: 0.65, days_used: 12, weeks_used: 4,
    ...overrides,
  }
}

/** The weekly report's `body_arousal`, as `_body_arousal` sends it; `measured` unless overridden. */
export function buildBodyArousal(overrides = {}) {
  return {
    high_share: 0.22, moderate_share: 0.3, classified_windows: 120, calibrating_windows: 6,
    state: 'measured', pending: false, few_readings: false,
    ...overrides,
  }
}

/** The weekly report -- what `WeeklySignalReport` renders, in `/weekly-report`'s shape. */
export function buildWeeklyReport(overrides = {}) {
  return {
    days: 7,
    // Heart figures are highlights, not averages, in the real payload.
    averages: { focus: 0.7, stress: 0.4, engagement: 0.6 },
    highlights: { dominant_emotion: 'happy', heart_rate_bpm: 72, rmssd_ms: 41 },
    body_arousal: buildBodyArousal(),
    sample_counts: { cognitive: WEEK_OF_SAMPLES, face: WEEK_OF_SAMPLES, heart: 1200, sessions: 3 },
    daily: [],
    emotion_distribution: { happy: 0.6, neutral: 0.4 },
    heart_sources: ['muse_optics'],
    sessions_recorded: 3,
    // `false` means the query failed.
    retrieved: { cognitive: true, face: true, heart: true, sessions: true },
    consent_retrieved: true,
    eeg_enabled: true,
    eeg_revoked_at: null,
    emotion_included: true,
    emotion_revoked_at: null,
    heart_included: true,
    heart_revoked_at: null,
    summary: 'A steady week.',
    ...overrides,
  }
}

/** `offLabel` inputs, named for the state each must produce. */
export const CHANNEL_REASONS = {
  /** Outranks everything below. */
  unreadable: { on: true, revokedAt: null, consentRetrieved: false, samples: 0 },
  revoked: { on: false, revokedAt: '2026-08-01T10:00:00Z', consentRetrieved: true, samples: 0 },
  /** Never 'Off since undefined'. */
  revokedUndated: { on: false, revokedAt: null, consentRetrieved: true, samples: 0 },
  calibrating: { on: true, revokedAt: null, consentRetrieved: true, samples: 42 },
  /** Consent on, nothing left: a parent erased it. */
  erased: { on: true, revokedAt: null, consentRetrieved: true, samples: 0, erasedAt: '2026-10-07T00:40:00Z' },
  noSensor: { on: true, revokedAt: null, consentRetrieved: true, samples: 0 },
}

/** The string each state above must render. */
export const CHANNEL_LABELS = {
  unreadable: 'Unavailable',
  revoked: 'Off since',
  revokedUndated: 'Not recorded',
  calibrating: 'Calibrating',
  erased: 'Erased ',
  noSensor: 'No sensor',
}
