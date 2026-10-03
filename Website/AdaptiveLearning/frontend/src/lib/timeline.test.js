import { it, expect } from 'vitest'
import { downsample } from './timeline'

// An hour at 4 Hz with a recording gap and three sparse heart readings, as buildTimeline shapes it.
function session() {
  const rows = []
  for (let i = 0; i < 14_400; i++) {
    const gap = i >= 7000 && i < 7400
    rows.push({ t: i * 250, focus: gap ? null : 0.5 + 0.1 * Math.sin(i / 50), stress: gap ? null : 0.3 })
  }
  rows[3000].focus = 0.99                               // a spike the chart must still show
  rows[9000].focus = 0.01
  for (const i of [100, 6000, 12_000]) Object.assign(rows[i], { heart_rate_bpm: 70, rmssd_ms: 40 })
  return rows
}

it('leaves a short series alone', () => {
  const rows = session().slice(0, 1000)
  expect(downsample(rows, 1500)).toBe(rows)
})

it('cuts an hour to within the budget, in order', () => {
  const out = downsample(session(), 1500)
  expect(out.length).toBeLessThanOrEqual(1500 + 3)
  // At least the first row of each of the floor(1500 / 7) buckets.
  expect(out.length).toBeGreaterThanOrEqual(Math.floor(1500 / 7))
  expect(out.every((r, i) => i === 0 || r.t > out[i - 1].t)).toBe(true)
})

it('keeps every heart reading, the extremes, and a null inside the gap', () => {
  const out = downsample(session(), 1500)
  expect(out.filter(r => r.heart_rate_bpm !== undefined)).toHaveLength(3)
  expect(out.some(r => r.focus === 0.99)).toBe(true)
  expect(out.some(r => r.focus === 0.01)).toBe(true)
  // Without a null between the two sides, recharts would join the line across the gap.
  expect(out.some(r => r.t >= 7000 * 250 && r.t < 7400 * 250 && r.focus === null)).toBe(true)
})
