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

/**
 * What a failed consent read looks like.
 *
 * `_consent()` fails closed, so this is a complete, plausible payload with
 * every channel off and no date — not an error shape. That's what makes it
 * risky to render wrong, and why testing with `{...ALL_ON, retrieved: false}`
 * wouldn't prove anything: those channels being on couldn't be misreported.
 */
export const CONSENT_READ_FAILED = {
  student_id: 'stu-1',
  retrieved: false,
  channels: {
    eeg: channel({ enabled: false }),
    headband_optical: channel({ enabled: false }),
    camera: channel({ enabled: false }),
  },
}

/** What the erase endpoint returns. `charts_failed: 0` is the good outcome --
 *  readings can be erased while an archived SVG resists deletion, and that is
 *  reported separately from an outright failure. */
export function buildErasureResult(overrides = {}) {
  return { erased: true, rows_deleted: 1200, charts_failed: 0, ...overrides }
}
