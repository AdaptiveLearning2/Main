import { it, expect } from 'vitest'
import { offLabel } from './signalFormat'
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
