/**
 * The signal payload shapes, and the four states a tile can be in.
 *
 * Builders rather than constants: every interesting test case is one field
 * away from the happy path, and restating the whole payload to move one
 * field tends to move two by accident.
 */

/** Seven days at 1 Hz — well above any per-request row cap, so a count
 *  computed from rows instead of the aggregate can't accidentally match. */
export const WEEK_OF_SAMPLES = 51840

/** The roster/dashboard summary — what `/api/students/{id}/signal-summary`
 *  and the batch `my_children` RPC return per student. */
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
    // Per-channel read outcomes. `false` means the query failed, not that
    // it read fine and found nothing.
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

/**
 * The four `offLabel` inputs, named for the state each must produce (not
 * the field values), since the mapping between them is what's under test.
 */
export const CHANNEL_REASONS = {
  /** Consent read failed. Outranks everything below — claiming a student
   *  switched something off is not something a failed query can say. */
  unreadable: { on: true, revokedAt: null, consentRetrieved: false, samples: 0 },
  /** Consent withdrawn, with the date it happened. */
  revoked: { on: false, revokedAt: '2026-08-01T10:00:00Z', consentRetrieved: true, samples: 0 },
  /** Withdrawn, but no date recorded. Renders 'Not recorded', not a
   *  half-written 'Off since undefined'. */
  revokedUndated: { on: false, revokedAt: null, consentRetrieved: true, samples: 0 },
  /** On, read, readings arrived, none usable yet — a rejected window or a
   *  baseline still forming. */
  calibrating: { on: true, revokedAt: null, consentRetrieved: true, samples: 42 },
  /** On, read, and nothing produced anything at all. */
  noSensor: { on: true, revokedAt: null, consentRetrieved: true, samples: 0 },
}

/** The rendered string each state above must produce, so a test can assert
 *  the mapping without restating the copy. */
export const CHANNEL_LABELS = {
  unreadable: 'Unavailable',
  revoked: 'Off since',
  revokedUndated: 'Not recorded',
  calibrating: 'Calibrating',
  noSensor: 'No sensor',
}
