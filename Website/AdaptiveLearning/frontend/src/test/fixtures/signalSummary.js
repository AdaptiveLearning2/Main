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

/** The weekly report -- what `WeeklySignalReport` renders. */
export function buildWeeklyReport(overrides = {}) {
  return {
    days: 7,
    averages: { focus: 0.7, stress: 0.4, engagement: 0.6, heart_rate: 72, rmssd: 41 },
    highlights: { dominant_emotion: 'happy' },
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
  noSensor: { on: true, revokedAt: null, consentRetrieved: true, samples: 0 },
}

/** The string each state above must render. */
export const CHANNEL_LABELS = {
  unreadable: 'Unavailable',
  revoked: 'Off since',
  revokedUndated: 'Not recorded',
  calibrating: 'Calibrating',
  noSensor: 'No sensor',
}
