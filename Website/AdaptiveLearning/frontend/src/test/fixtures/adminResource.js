/** The admin payloads that `useAdminResource` loads. */

/** `GET /api/admin/flags`. The defaults are the values the system had before
 *  the table existed, so a flag absent from the response still has one. */
export function buildFlags(overrides = {}) {
  return {
    flags: [
      { key: 'strategy_llm_enabled', enabled: false, bypass_until: null, description: 'Model pass.' },
      { key: 'recording_eeg_enabled', enabled: true, bypass_until: null },
      { key: 'recording_heart_enabled', enabled: true, bypass_until: null },
      { key: 'recording_camera_enabled', enabled: true, bypass_until: null },
      { key: 'consent_enforcement_enabled', enabled: true, bypass_until: null },
    ],
    consent_enforcement_active: true,
    ...overrides,
  }
}

/**
 * `GET /api/admin/retention-window`.
 *
 * `state` has six values but only `open` and `not_enforced` permit
 * recording. The other four each deny for a different reason (e.g. "year
 * hasn't started" vs "couldn't read the setting") that a reader must be
 * able to tell apart.
 */
export function buildRetentionWindow(overrides = {}) {
  return {
    enforced: true,
    starts_on: '2026-09-01',
    ends_on: '2027-07-20',
    timezone: 'Europe/London',
    state: 'open',
    ...overrides,
  }
}

export const RETENTION_STATES = [
  'open', 'not_enforced', 'before_year', 'after_year', 'unconfigured', 'unreadable',
]

/** The two states that permit recording, so a test asserting the copy cannot
 *  quietly drift into asserting the gate. */
export const RECORDING_STATES = ['open', 'not_enforced']
