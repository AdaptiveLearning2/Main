/**
 * The signal payload shapes, and the four states a tile can be in.
 *
 * `offLabel` is the reason these are builders rather than constants: every
 * interesting case is one field away from the happy path, and a test that
 * restates the whole payload to move one field tends to move two.
 */

/** Seven days at 1 Hz. Far above any per-request row cap, so a count rendered
 *  from rows rather than from the aggregate cannot accidentally match. */
export const WEEK_OF_SAMPLES = 51840

/** The roster/dashboard summary -- what `/api/students/{id}/signal-summary`
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
    // Per-channel read outcomes. `false` here is a failed query, which is not
    // the same fact as a channel that read fine and held nothing.
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
 * The four `offLabel` inputs, by the state each one must produce.
 *
 * Named for the *state*, not the field values, because the mapping is the thing
 * under test: `unreadable` and `noSensor` differ only in fields a reader would
 * not guess were load-bearing, and the priority order between them is the
 * property that has been wrong before.
 */
export const CHANNEL_REASONS = {
  /** The consent read failed. Outranks everything below it -- claiming a
   *  student switched something off is a claim a failed query has not earned. */
  unreadable: { on: true, revokedAt: null, consentRetrieved: false, samples: 0 },
  /** Consent withdrawn, with the date it happened. */
  revoked: { on: false, revokedAt: '2026-08-01T10:00:00Z', consentRetrieved: true, samples: 0 },
  /** Withdrawn, but nothing recorded when. Renders 'Not recorded', not a
   *  half-written 'Off since undefined'. */
  revokedUndated: { on: false, revokedAt: null, consentRetrieved: true, samples: 0 },
  /** On, read, readings arrived, none usable yet -- a rejected window or a
   *  baseline still forming. */
  calibrating: { on: true, revokedAt: null, consentRetrieved: true, samples: 42 },
  /** On, read, and nothing produced anything at all. */
  noSensor: { on: true, revokedAt: null, consentRetrieved: true, samples: 0 },
}

/** The rendered string each of the above must produce, so a test can assert the
 *  mapping without restating the copy and no two states may collapse. */
export const CHANNEL_LABELS = {
  unreadable: 'Unavailable',
  revoked: 'Off since',
  revokedUndated: 'Not recorded',
  calibrating: 'Calibrating',
  noSensor: 'No sensor',
}
