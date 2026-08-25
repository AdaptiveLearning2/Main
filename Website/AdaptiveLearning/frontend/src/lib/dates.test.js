import { describe, it, expect } from 'vitest'
import { fmtDate } from './dates'

describe('fmtDate', () => {
  it('answers null rather than a placeholder for a missing or bad date', () => {
    // Every use site reads `{fmtDate(x) && <> on {fmtDate(x)}</>}`, so a
    // placeholder would render "on Invalid Date" instead of nothing at all.
    expect(fmtDate(null)).toBeNull()
    expect(fmtDate(undefined)).toBeNull()
    expect(fmtDate('')).toBeNull()
    expect(fmtDate('not a date')).toBeNull()
  })

  it('formats a real date', () => {
    expect(fmtDate('2026-03-14T10:00:00Z')).toMatch(/March/)
  })
})
