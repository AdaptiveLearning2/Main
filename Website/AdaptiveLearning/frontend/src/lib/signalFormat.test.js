import { it, expect } from 'vitest'
import { offLabel, calmPct, calmRatio } from './signalFormat'
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
