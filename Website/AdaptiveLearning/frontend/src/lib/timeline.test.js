import { it, expect } from 'vitest'
import { downsample } from './timeline'

// An hour at 4 Hz with three sparse heart readings, as buildTimeline shapes it.
function session() {
  const rows = []
  for (let i = 0; i < 14_400; i++) {
    rows.push({ t: i * 250, focus: 0.5 + 0.1 * Math.sin(i / 50), calm: 0.7 })
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
  expect(out.length).toBeLessThanOrEqual(1500)
  expect(out.length).toBeGreaterThanOrEqual(Math.floor(1500 / 9))
  expect(out.every((r, i) => i === 0 || r.t > out[i - 1].t)).toBe(true)
})

it('keeps every sparse heart reading and the extremes', () => {
  const out = downsample(session(), 1500)
  expect(out.filter(r => r.heart_rate_bpm !== undefined)).toHaveLength(3)
  expect(out.some(r => r.focus === 0.99)).toBe(true)
  expect(out.some(r => r.focus === 0.01)).toBe(true)
})

it('holds the budget when every row carries a heart reading, and still draws heart', () => {
  // Merged rows from a dense heart channel, one reading per cognitive row.
  const rows = session().map((r, i) => ({ ...r, heart_rate_bpm: 70 + (i % 10), rmssd_ms: 40 + (i % 7) }))
  rows[5000].heart_rate_bpm = 140
  rows[8000].rmssd_ms = 5
  const out = downsample(rows, 1500)
  expect(out.length).toBeLessThanOrEqual(1500)
  expect(out.some(r => r.heart_rate_bpm === 140)).toBe(true)
  expect(out.some(r => r.rmssd_ms === 5)).toBe(true)
  // Spread across the session, not a handful of points.
  expect(out.filter(r => typeof r.heart_rate_bpm === 'number').length).toBeGreaterThan(1500 / 9)
})

it('always keeps the last row, so the line reaches the end of the session', () => {
  // Flat series: no extreme lands on the last row, so only an explicit rule keeps it.
  const rows = Array.from({ length: 14_400 }, (_, i) => ({ t: i * 250, focus: 0.5, calm: 0.7 }))
  const out = downsample(rows, 1500)
  expect(out.at(-1)).toBe(rows.at(-1))
  expect(out[0]).toBe(rows[0])
})
