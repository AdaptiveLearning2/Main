/** The three consent channels, named for the sensor rather than the signal. */
export const CHANNELS = ['eeg', 'headband_optical', 'camera']

function channel(overrides = {}) {
  return { enabled: true, revoked_at: null, revoked_by: null, erased_at: null, ...overrides }
}

export function buildConsentState(overrides = {}) {
  return {
    student_id: 'stu-1',
    retrieved: true,
    channels: {
      eeg: channel(),
      headband_optical: channel(),
      camera: channel(),
      ...(overrides.channels || {}),
    },
    ...overrides,
  }
}

/** One channel withdrawn, the rest untouched. */
export function withChannelOff(payload, name, {
  revoked_at = '2026-08-01T10:00:00Z',
  revoked_by = 'student',
} = {}) {
  return {
    ...payload,
    channels: {
      ...payload.channels,
      [name]: { ...payload.channels[name], enabled: false, revoked_at, revoked_by },
    },
  }
}

/** A failed consent read: fails closed, so a plausible all-off payload, not an error shape. */
export const CONSENT_READ_FAILED = {
  student_id: 'stu-1',
  retrieved: false,
  channels: {
    eeg: channel({ enabled: false }),
    headband_optical: channel({ enabled: false }),
    camera: channel({ enabled: false }),
  },
}

/** The erase endpoint's response. `charts_failed: 0` is the good outcome. */
export function buildErasureResult(overrides = {}) {
  return { erased: true, rows_deleted: 1200, charts_failed: 0, ...overrides }
}
