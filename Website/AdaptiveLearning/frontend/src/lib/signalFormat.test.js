import { it, expect } from 'vitest'
import { offLabel, calmPct, calmRatio, isPaused, pausedNotice } from './signalFormat'
import { CHANNEL_REASONS, CHANNEL_LABELS } from '../test/fixtures/signalSummary'

it.each(Object.keys(CHANNEL_REASONS))('labels the %s state', (state) => {
  expect(offLabel(CHANNEL_REASONS[state]).startsWith(CHANNEL_LABELS[state])).toBe(true)
})

it('lets a withdrawal, an unreadable consent and samples since all outrank an erasure', () => {
  const at = '2026-10-07T00:40:00Z'
  expect(offLabel({ ...CHANNEL_REASONS.revoked, erasedAt: at })).toMatch(/^Off since/)
  expect(offLabel({ ...CHANNEL_REASONS.unreadable, erasedAt: at })).toBe('Unavailable')
  expect(offLabel({ ...CHANNEL_REASONS.calibrating, erasedAt: at })).toBe('Calibrating')
})

it.each([
  [null, 'N/A'],
  ['', 'N/A'],
  [undefined, 'N/A'],
  [0, '100%'],
  [1, '0%'],
  [0.3, '70%'],
])('calmPct(%o) is %s: an absence never becomes 100%% calm', (stress, shown) => {
  expect(calmPct(stress)).toBe(shown)
})

it('calmRatio keeps null as null', () => {
  expect(calmRatio(null)).toBeNull()
  expect(calmRatio(0.25)).toBe(0.75)
})

it('puts a pause by the school above every consent state, since the switch is the cause', () => {
  const states = [
    { on: true, consentRetrieved: true, samples: 0 },
    { on: false, revokedAt: '2026-10-01T00:00:00Z', consentRetrieved: true },
    { on: true, consentRetrieved: false },
    { on: true, consentRetrieved: true, samples: 10 },
  ]
  for (const s of states) expect(offLabel({ ...s, paused: true })).toBe('Paused by the school')
  expect(offLabel({ on: true, consentRetrieved: true, samples: 0 })).toBe('No sensor')
})

it('names only the channels that are paused, and nothing for a missing or unknown list', () => {
  expect(pausedNotice(['eeg', 'heart'])).toMatch(/focus and calm, heart rate is paused by the school/)
  expect(pausedNotice(['emotion'])).toMatch(/facial expression/)
  for (const none of [undefined, null, [], ['nonsense']]) expect(pausedNotice(none)).toBeNull()
  expect(isPaused({ paused_channels: ['heart'] }, 'heart')).toBe(true)
  expect(isPaused({ paused_channels: ['heart'] }, 'eeg')).toBe(false)
  expect(isPaused({}, 'eeg')).toBe(false)
})
