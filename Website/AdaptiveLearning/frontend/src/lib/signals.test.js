/** The recorder brings the stream up (`record: false`) and arms recording (`record: true`) as two calls. */
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
    // Anything but a refusal: the probe ran and the sidecar is not there.
    overrideApi('/api/eeg/health', fail(500))
    expect(await eegHealth()).toMatchObject({ available: false })
  })

  it('does not call the headband offline because the probe was rate limited', async () => {
    // A 429 says how often this was asked, nothing about the hardware.
    overrideApi('/api/eeg/health', fail(429))

    const h = await eegHealth()

    expect(h.refused).toBe(true)
    expect(h).not.toHaveProperty('available')
  })

  it('marks a probe that never landed, so it cannot pass as an answer', async () => {
    // The backend's config-fault answer has the same fields, so the catch marks itself.
    overrideApi('/api/eeg/health', fail(500))
    expect(await eegHealth()).toMatchObject({ answered: false, available: false })
  })

  it('leaves an answer alone, including one that reports a fault', async () => {
    // A real answer must not acquire `answered: false`.
    overrideApi('/api/eeg/health', () => ({
      available: false, url: 'http://localhost:8001', error: 'EEG_API_TOKEN is not set',
    }))
    const h = await eegHealth()
    expect(h.answered).toBeUndefined()
    expect(h.error).toBe('EEG_API_TOKEN is not set')
  })
})
