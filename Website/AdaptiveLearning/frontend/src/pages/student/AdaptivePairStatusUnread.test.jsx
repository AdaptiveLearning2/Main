/**
 * A status read that did not land is not "no link to adopt".
 *
 * Under pull `hw.status()` was `(await eegStatus(stationId))?.muse || {}`, and
 * `eegStatus` answers with its own fallback rather than throwing -- so the
 * `.catch(() => null)` each caller wraps it in was structurally dead, and an
 * unreachable backend arrived at the adoption check as an empty answer. The
 * fall-through from there disconnects and rescans, so one failed request at
 * the moment of the click tore down a headband that was streaming: the
 * "connects, then immediately disconnects" the adoption block exists to stop,
 * recorded on hardware as three clicks to pair.
 *
 * The same empty answer fed the two loops below it -- twelve unlanded reads
 * read as twelve empty ones, so a reachable-and-fine headband was reported as
 * "No headband found", and the connect poll's failure tells a student to
 * power-cycle hardware that is working.
 */
import { it, expect, beforeEach, vi } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'

vi.mock('../../lib/api', async () => await import('../../test/mocks/apiFetch'))
vi.mock('../../lib/supabase', async () => await import('../../test/mocks/supabase'))
vi.mock('sonner', () => ({ toast: { error: vi.fn(), success: vi.fn(), warning: vi.fn() } }))
vi.mock('../../lib/session', () => ({
  endSession: vi.fn(async () => true),
  recordAnswer: vi.fn(async () => ({ topic: 'ordering' })),
}))
vi.mock('../../lib/signals', () => ({
  // `start` has to resolve to a running session: the click throws before it
  // reaches the adoption check otherwise, and the test would pass on the
  // wrong error.
  createSignalRecorder: () => ({
    start: vi.fn(async () => ({ ok: true, running: true })), stop: vi.fn(),
  }),
  eegHealth: vi.fn(async () => ({ available: true, ingest_mode: 'pull' })),
  eegStatus: vi.fn(async () => ({})),
  eegDevices: vi.fn(async () => ({ available: true, devices: [] })),
}))
vi.mock('../../lib/sidecar', () => ({
  startPush: vi.fn(async () => ({})), stopPush: vi.fn(async () => ({})),
  stopPushOnUnload: vi.fn(),
  pushStatus: vi.fn(async () => ({})),
  deviceStart: vi.fn(async () => ({})), deviceStop: vi.fn(async () => ({})),
  deviceStopOnUnload: vi.fn(),
  museRefresh: vi.fn(async () => ({})), museConnect: vi.fn(async () => ({})),
  museDisconnect: vi.fn(async () => ({})),
  museState: vi.fn(async () => ({ running: false, ingestion: {} })),
  museStatus: vi.fn(async () => ({})),
  devices: vi.fn(async () => []),
  releasePushIfIdle: vi.fn(async () => ({})),
  sidecarDebug: vi.fn(async () => ({})),
}))
vi.mock('../../context/AuthContext', () => ({
  useAuth: () => ({ user: { id: 'u1', email: 'a@b.c' }, role: 'student', loading: false }),
}))

import { toast } from 'sonner'
import { apiFetch, mockApi, resetApi } from '../../test/mocks/apiFetch'
import { eegStatus } from '../../lib/signals'
import Adaptive from './Adaptive'

const called = (path) => apiFetch.mock.calls.some(([p]) => p.startsWith(path))

beforeEach(() => {
  resetApi()
  vi.clearAllMocks()
  mockApi({
    'GET /api/profile/me': () => ({ id: 'u1', role: 'student', grade_level: '1st Grade' }),
    'GET /api/performance/student/u1': () => [],
    'POST /api/sessions/start': () => ({ id: 'sess-pair' }),
    'GET /api/eeg/health': () => ({ available: true, ingest_mode: 'pull' }),
    'GET /api/eeg/status': () => ({}),
    // Routed so the teardown *can* happen: unrouted, the double throws and the
    // assertions below would pass on a crash rather than on the guard.
    'POST /api/eeg/muse/disconnect': () => ({ ok: true }),
    'POST /api/eeg/muse/refresh': () => ({ ok: true }),
    'POST /api/eeg/muse/connect': () => ({ ok: true }),
  })
})

it('leaves a live link alone when the status read did not land', async () => {
  // The swallowed fallback carries no `muse`, which `|| {}` turned into an
  // answer meaning "nothing is connected".
  eegStatus.mockResolvedValue({ answered: false, service: false, poller: { running: false } })
  render(<Adaptive />)

  const button = await screen.findByRole('button', { name: /connect headband/i })
  await waitFor(() => expect(button).not.toBeDisabled())
  fireEvent.click(button)

  // The teardown is the damage, so it is what the assertion names -- and the
  // wait has to outlast the path the bug takes, or the failure is a timeout
  // rather than the disconnect this test exists to catch. Unguarded, the
  // click falls through to the 1.5 s settle and the 12 s scan.
  await waitFor(() => expect(toast.error).toHaveBeenCalled(), { timeout: 20000 })
  expect(called('/api/eeg/muse/disconnect')).toBe(false)
  expect(called('/api/eeg/muse/refresh')).toBe(false)

  // And the message names the check rather than the hardware: the other two
  // send a student to move or power-cycle a headband that is working.
  const [title, opts] = toast.error.mock.calls.at(-1)
  expect(`${title} ${opts?.description ?? ''}`).toMatch(/EEG service/)
  expect(`${title} ${opts?.description ?? ''}`).not.toMatch(/power|Bluetooth|No headband found/i)
}, 30000)

const UNLANDED = { answered: false, service: false, poller: { running: false } }

it('does not report an empty scan from twelve reads that never landed', async () => {
  // Past the adoption check this time: the first read lands and says nothing
  // is connected, so the disconnect and the scan are correct. What follows is
  // not -- `st?.ingestion?.muse_devices || []` read every unlanded reply as an
  // empty scan, so the loop ran its full twelve seconds and reported
  // `no_device`, whose instruction sends a student to check a headband that
  // is switched on, in range, and fine.
  eegStatus.mockImplementation(async () =>
    called('/api/eeg/muse/refresh') ? UNLANDED : { muse: { ingestion: {} } })
  render(<Adaptive />)

  const button = await screen.findByRole('button', { name: /connect headband/i })
  await waitFor(() => expect(button).not.toBeDisabled())
  fireEvent.click(button)

  await waitFor(() => expect(called('/api/eeg/muse/refresh')).toBe(true), { timeout: 8000 })
  await waitFor(() => expect(toast.error).toHaveBeenCalled(), { timeout: 25000 })

  const [title, opts] = toast.error.mock.calls.at(-1)
  const said = `${title} ${opts?.description ?? ''}`
  expect(said).toMatch(/EEG service/)
  expect(said).not.toMatch(/No headband found/i)
  // 60 s for ~14 s of real waiting: this file runs on real timers, like
  // `AdaptiveReconnect.test.jsx`, and the headroom costs nothing on a passing
  // assertion but is the whole difference under a loaded full-suite run.
}, 60000)

it('does not blame the firmware for a connect poll that never landed', async () => {
  // The last reader, and the one with the most disruptive instruction: ten
  // unlanded reads read as ten "still not connected" ones, so `not_connected`
  // told a student to hold the power button until the headband switches off
  // -- a claim about its firmware that no read here established.
  eegStatus.mockImplementation(async () =>
    called('/api/eeg/muse/connect')
      ? UNLANDED
      : { muse: { ingestion: { muse_devices: ['Muse-1234'] } } })
  render(<Adaptive />)

  const button = await screen.findByRole('button', { name: /connect headband/i })
  await waitFor(() => expect(button).not.toBeDisabled())
  fireEvent.click(button)

  await waitFor(() => expect(called('/api/eeg/muse/connect')).toBe(true), { timeout: 15000 })
  await waitFor(() => expect(toast.error).toHaveBeenCalled(), { timeout: 25000 })

  const [title, opts] = toast.error.mock.calls.at(-1)
  const said = `${title} ${opts?.description ?? ''}`
  expect(said).toMatch(/EEG service/)
  expect(said).not.toMatch(/power/i)
}, 60000)
