/** Every field the live monitor reads from a `latest_*` payload is one the latest-signals RPC builds. */
import { it, expect, beforeEach, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'

vi.mock('../../lib/api', async () => await import('../../test/mocks/apiFetch'))

import { mockApi, resetApi } from '../../test/mocks/apiFetch'
// Also held equal to the SQL by backend/tests/test_live_payload_fields.py.
import FIELDS from '../../test/fixtures/livePayloadFields.json'
import Live from './Live'

let reads = {}

/** `payload` behind a proxy noting each key read, nested objects under `channel.key`. */
function recorded(channel, payload) {
  for (const key of Object.keys(payload)) expect(FIELDS[channel], `${channel}.${key}`).toContain(key)
  const nested = Object.fromEntries(Object.entries(payload)
    .filter(([, value]) => value && typeof value === 'object')
    .map(([key, value]) => [key, recorded(`${channel}.${key}`, value)]))
  const note = key => { if (typeof key === 'string') (reads[channel] ||= new Set()).add(key) }
  return new Proxy(payload, {
    get(target, key) { note(key); return Object.hasOwn(nested, key) ? nested[key] : target[key] },
    has(target, key) { note(key); return key in target },
  })
}

function roster() {
  const ts = new Date().toISOString()
  const student = (id, payloads) => ({
    user_id: id, name: `Student ${id}`, email: `${id}@x.y`, active_session: { id: `sess-${id}` },
    latest_cognitive: null, latest_face: null, latest_heart: null, ...payloads,
  })
  return [
    // Good contact: eegWeak stops at `signal_quality`, then at a non-null focus.
    student('a', {
      latest_cognitive: recorded('cognitive', { ts, focus: 0.6, stress: 0.3, engagement: 0.6,
        raw: { signal_quality: 'good', quality_basis: 'contact' } }),
      latest_face: recorded('face', { ts, emotion: 'happy' }),
      latest_heart: recorded('heart', { ts, source: 'muse_optics', trusted: true,
        heart_rate_bpm: 72, rmssd_ms: 40 }),
    }),
    // A contact verdict: the only path that reads `quality_basis` to its end.
    student('b', {
      latest_cognitive: recorded('cognitive', { ts, focus: null, stress: null, engagement: null,
        raw: { signal_quality: 'poor', quality_basis: 'contact' } }),
      latest_heart: recorded('heart', { ts, source: 'rppg', trusted: false,
        heart_rate_bpm: null, rmssd_ms: null }),
    }),
    // A heuristic verdict on a nulled row: the path that reads every measurement.
    student('c', {
      latest_cognitive: recorded('cognitive', { ts, focus: null, stress: null, engagement: null,
        raw: { signal_quality: 'poor', quality_basis: 'heuristic' } }),
    }),
  ]
}

beforeEach(() => {
  resetApi()
  reads = {}
})

it('reads nothing from a live payload that latest_signals_for_sessions does not build', async () => {
  mockApi({
    '/api/classes': () => [{ id: 'c1', name: 'Year 4' }],
    '/api/teacher/classes/c1/live': roster,
  })
  render(<MemoryRouter><Live /></MemoryRouter>)
  // Each card's last section is on screen, so every read path has run.
  await screen.findByText(/72 bpm/)
  await screen.findByText(/HRV 40 ms/)
  await screen.findByText('happy')
  expect(await screen.findAllByText(/weak signal/)).toHaveLength(3)

  for (const [channel, keys] of Object.entries(reads)) {
    expect([...keys].filter(key => !FIELDS[channel]?.includes(key)), channel).toEqual([])
  }
  // A recorder that saw nothing would pass the check above.
  for (const channel of ['cognitive', 'cognitive.raw', 'face', 'heart']) {
    expect(reads[channel]?.size ?? 0, channel).toBeGreaterThan(0)
  }
})
