import { it, expect } from 'vitest'
import { stagger, STAGGER_CAP } from './stagger'

it('staggers the first rows as before', () => {
  expect(stagger(0, 0.04)).toBe(0)
  expect(stagger(3, 0.05, 0.2)).toBeCloseTo(0.35)
})

it('stops growing after the cap, so a long list is visible within a second', () => {
  const last = stagger(199, 0.04)
  expect(last).toBeCloseTo(STAGGER_CAP * 0.04)
  expect(last).toBeLessThan(1)
  expect(stagger(STAGGER_CAP + 50, 0.07, 0.55)).toBe(stagger(STAGGER_CAP, 0.07, 0.55))
})
