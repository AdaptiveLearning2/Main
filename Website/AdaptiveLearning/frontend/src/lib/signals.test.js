/**
 * The recorder brings the stream up and arms recording as two calls.
 *
 * Under pull the backend's poller both keeps the sidecar's device stream up
 * (pairing needs that) and writes rows, and only the second belongs to a
 * session. Connect asks for the first with `record: false`; the first question
 * sends `record: true`, which arms the running poller in place.
 */
import { describe, it, expect, beforeEach, vi } from 'vitest'

vi.mock('./api', async () => await import('../test/mocks/apiFetch'))
import { apiFetch, mockApi, overrideApi, resetApi } from '../test/mocks/apiFetch'
import { createSignalRecorder, eegHealth } from './signals'

const startBodies = () => apiFetch.mock.calls
  .filter(([path]) => path === '/api/eeg/start')
  .map(([, opts]) => opts.body)

beforeEach(() => {
  resetApi()
  mockApi({
    'POST /api/eeg/start': () => ({ ok: true, running: true }),
    'POST /api/eeg/stop': () => ({ ok: true }),
  })
})

describe('createSignalRecorder', () => {
  it('sends the record flag it was asked for, and defaults to recording', async () => {
    const rec = createSignalRecorder({ sessionId: 's1', deviceId: 'default' })
    await rec.start({ record: false })
    await rec.start()
    expect(startBodies()).toEqual([
      { session_id: 's1', device_id: 'default', record: false },
      { session_id: 's1', device_id: 'default', record: true },
    ])
    expect(rec.isActive()).toBe(true)
    expect(rec.isRecording()).toBe(true)
  })

  it('arms a stream that is already up rather than treating start as a no-op', async () => {
    const rec = createSignalRecorder({ sessionId: 's1', deviceId: 'default' })
    await rec.start({ record: false })
    expect(rec.isRecording()).toBe(false)
    const res = await rec.start({ record: true })
    expect(res.ok).toBe(true)
    expect(startBodies()).toHaveLength(2)
    expect(rec.isRecording()).toBe(true)
    // Same state asked for twice is not sent twice.
    await rec.start({ record: true })
    expect(startBodies()).toHaveLength(2)
  })

  it('names its session, so a page can tell a recorder from a finished one', async () => {
    const rec = createSignalRecorder({ sessionId: 's1', deviceId: 'default' })
    expect(rec.sessionId).toBe('s1')
    await rec.start({ record: true })
    await rec.stop()
    expect(rec.isActive()).toBe(false)
    expect(rec.isRecording()).toBe(false)
  })
})


describe('eegHealth, and what a failed probe is allowed to claim', () => {
  const fail = (status) => () => {
    throw Object.assign(new Error('nope'), { status })
  }

  it('reports the sidecar as unavailable when the probe reached the backend', async () => {
    // A 500, a dropped connection, anything that is not a refusal: the probe
    // ran and the answer is that the sidecar is not there.
    overrideApi('/api/eeg/health', fail(500))
    expect(await eegHealth()).toMatchObject({ available: false })
  })

  it('does not call the headband offline because the probe was rate limited', async () => {
    // The state this feeds decides whether the page says "offline" and whether
    // Connect is offered. A 429 is a fact about how often this endpoint was
    // asked -- every open lesson asks it every 5 s -- and says nothing about
    // the hardware. `apiFetch` retries only 503, so nothing upstream softens
    // this either.
    overrideApi('/api/eeg/health', fail(429))

    const h = await eegHealth()

    expect(h.refused).toBe(true)
    expect(h).not.toHaveProperty('available')
  })
})
