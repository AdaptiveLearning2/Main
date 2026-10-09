import { MEASURES, AROUSAL_REASONS, formatMeasure, usualWords, usualRange } from './signalGlossary'

describe('MEASURES', () => {
  it('names each measure once, and none of them "Stress"', () => {
    // EEG stress is shown as calm; heart arousal has its own name (CLAUDE.md).
    const names = Object.values(MEASURES).map(m => m.name)
    expect(new Set(names).size).toBe(names.length)
    for (const name of names) expect(name).not.toMatch(/stress/i)
  })

  it('says body arousal rises with more than stress', () => {
    expect(MEASURES.body_arousal.caveat).toMatch(/excitement, effort and movement/)
  })

  it('defines body arousal as readings above the lesson start, not time above resting', () => {
    expect(MEASURES.body_arousal.what).toMatch(/start of each lesson/)
    expect(MEASURES.body_arousal.what).not.toMatch(/resting|lesson time/)
  })

  it('gives every no-figure arousal state its own words', () => {
    const words = Object.values(AROUSAL_REASONS)
    expect(new Set(words).size).toBe(words.length)
    expect(Object.keys(AROUSAL_REASONS)).not.toContain('measured')
  })
})

describe('formatMeasure', () => {
  it.each([
    ['focus', 0.72, '72%'],
    ['heart_rate_bpm', 71.6, '72 bpm'],
    ['rmssd_ms', 41.4, '41 ms'],
    // Math.round(12.5) is 13, as the backend's verdict rounds; Python's round would give 12.
    ['calm', 0.125, '13%'],
  ])('%s %s prints %s', (key, value, out) => {
    expect(formatMeasure(key, value)).toBe(out)
  })

  it.each([null, undefined, 'x'])('has no figure for %s, never 0', (value) => {
    expect(formatMeasure('focus', value)).toBeNull()
  })
})

describe('usualWords', () => {
  it.each([
    [{ status: 'compared', verdict: 'about_usual' }, 'About usual'],
    [{ status: 'compared', verdict: 'higher' }, 'Higher than usual'],
    [{ status: 'compared', verdict: 'lower' }, 'Lower than usual'],
    [{ status: 'not_enough_history' }, 'Not enough history yet'],
    [{ status: 'too_little_this_period' }, 'Too few readings to compare'],
    [{ status: 'pending' }, "Compared once this week's lessons finish"],
    [{ status: 'not_retrieved' }, "Couldn't compare with earlier weeks"],
    [{ status: 'not_comparable', reason: 'mixed_scale' }, "Not comparable: the headband's scoring changed"],
    [{ status: 'not_comparable', reason: 'sensor_changed' }, 'Not comparable: more than one heart sensor'],
  ])('%o reads %s', (entry, words) => {
    expect(usualWords(entry)).toBe(words)
  })

  it.each([undefined, null, { status: 'no_current' }, { status: 'not_requested' }])(
    'says nothing for %o', (entry) => {
      expect(usualWords(entry)).toBeNull()
    })

  it('never calls a comparison good or bad', () => {
    for (const verdict of ['about_usual', 'higher', 'lower']) {
      expect(usualWords({ status: 'compared', verdict })).not.toMatch(/good|bad|better|worse/i)
    }
  })
})

describe('usualRange', () => {
  it('prints the range as the tile prints its value', () => {
    expect(usualRange('focus', { status: 'compared', low: 0.55, high: 0.65 })).toBe('Usual 55–65%')
    expect(usualRange('heart_rate_bpm', { status: 'compared', low: 70.4, high: 74.6 }))
      .toBe('Usual 70–75 bpm')
  })

  it('has no range for a measure that was not compared', () => {
    expect(usualRange('focus', { status: 'not_enough_history', low: null, high: null })).toBeNull()
    expect(usualRange('focus', undefined)).toBeNull()
  })
})
