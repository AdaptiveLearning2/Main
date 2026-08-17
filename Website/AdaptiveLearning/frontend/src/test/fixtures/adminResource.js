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
 * `state` is the part with six values and only two of them record: `open` and
 * `not_enforced` do, while `before_year`, `after_year`, `unconfigured` and
 * `unreadable` each deny for a different reason a reader has to be able to tell
 * apart -- "the year hasn't started" and "we couldn't read the setting" send a
 * parent to different places.
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
