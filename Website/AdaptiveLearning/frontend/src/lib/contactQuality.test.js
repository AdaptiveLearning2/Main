import { describe, it, expect } from 'vitest'
import { contactQuality } from './contactQuality'

describe('contactQuality', () => {
  it('is null, not poor, when nothing has been reported', () => {
    expect(contactQuality(null)).toBeNull()
    expect(contactQuality({})).toBeNull()
    expect(contactQuality({ hsi: null, is_good: null })).toBeNull()
    // HSI 0 on every channel is "not reported for this channel", same thing.
    expect(contactQuality({ hsi: [0, 0, 0, 0] })).toBeNull()
  })

  it('matches the sidecar thresholds on HSI alone', () => {
    expect(contactQuality({ hsi: [1, 1, 1, 1] })).toBe('good')
    // One mediocre electrode of four is 0.875, still good.
    expect(contactQuality({ hsi: [1, 1, 1, 2] })).toBe('good')
    // Two mediocre is 0.75: degraded.
    expect(contactQuality({ hsi: [1, 1, 2, 2] })).toBe('degraded')
    // The measured failure mode -- every electrode at 4 -- is poor.
    expect(contactQuality({ hsi: [4, 4, 4, 4] })).toBe('poor')
    expect(contactQuality({ hsi: [1, 4, 4, 4] })).toBe('poor')
  })

  it('takes the worse of the two measures when both report', () => {
    expect(contactQuality({ hsi: [1, 1, 1, 1], is_good: [1, 0, 0, 0] })).toBe('poor')
    expect(contactQuality({ hsi: [4, 4, 4, 4], is_good: [1, 1, 1, 1] })).toBe('poor')
    // Perfect fit, but one electrode's last second was unusable: 0.75, and
    // the sidecar calls that degraded, not good.
    expect(contactQuality({ hsi: [1, 1, 1, 1], is_good: [1, 1, 1, 0] })).toBe('degraded')
    expect(contactQuality({ hsi: [1, 1, 1, 1], is_good: [1, 1, 1, 1] })).toBe('good')
  })

  it('ignores a malformed array rather than reading it as poor', () => {
    expect(contactQuality({ is_good: ['x', 1, 1, 1] })).toBeNull()
    expect(contactQuality({ hsi: [1, 1, 1, 1], is_good: ['x'] })).toBe('good')
  })
})
