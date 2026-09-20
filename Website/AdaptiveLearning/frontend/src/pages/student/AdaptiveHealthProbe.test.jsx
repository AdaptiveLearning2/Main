/**
 * A refused health probe is not a headband that is offline.
 *
 * `/api/eeg/health` is polled every 5 s per open lesson, which makes it the
 * heaviest caller of any per-address rate limit on the public routes -- and
 * `eegHealth` used to fold every failure into `available: false`. A limit
 * reached anywhere near that endpoint therefore reported the *hardware* as
 * down, on every student's page at once, with nothing saying a limit caused
 * it. `apiFetch` retries only 503, so nothing upstream softened it either.
 *
 * Three states, not two: reachable, not reachable, and a probe that answered
 * neither.
 */
import { it, expect, beforeEach, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'

vi.mock('../../lib/api', async () => await import('../../test/mocks/apiFetch'))
vi.mock('../../lib/supabase', async () => await import('../../test/mocks/supabase'))
vi.mock('sonner', () => ({ toast: { error: vi.fn(), success: vi.fn() } }))
vi.mock('../../lib/session', () => ({
  endSession: vi.fn(async () => true),
  recordAnswer: vi.fn(async () => ({ topic: 'ordering' })),
}))
vi.mock('../../lib/signals', () => ({
  createSignalRecorder: () => ({ start: vi.fn(), stop: vi.fn() }),
  eegHealth: vi.fn(async () => ({ available: true, ingest_mode: 'pull' })),
  eegStatus: vi.fn(async () => ({})),
  eegDevices: vi.fn(async () => ({ devices: [] })),
}))
vi.mock('../../lib/sidecar', () => ({
  startPush: vi.fn(), stopPush: vi.fn(), stopPushOnUnload: vi.fn(),
  pushStatus: vi.fn(async () => ({})), deviceStart: vi.fn(), museRefresh: vi.fn(),
  museConnect: vi.fn(), museDisconnect: vi.fn(), museStatus: vi.fn(async () => ({})),
  sidecarDebug: vi.fn(async () => ({})),
}))
vi.mock('../../context/AuthContext', () => ({
  useAuth: () => ({ user: { id: 'u1', email: 'a@b.c' }, role: 'student', loading: false }),
}))

import { mockApi, resetApi } from '../../test/mocks/apiFetch'
import { eegHealth, eegDevices } from '../../lib/signals'
import Adaptive from './Adaptive'

beforeEach(() => {
  resetApi()
  vi.clearAllMocks()
  mockApi({
    'GET /api/profile/me': () => ({ id: 'u1', role: 'student', grade_level: '1st Grade' }),
    'GET /api/performance/student/u1': () => [],
    'POST /api/sessions/start': () => ({ id: 'sess-probe' }),
    'GET /api/eeg/health': () => ({ available: true, ingest_mode: 'pull' }),
    'GET /api/eeg/status': () => ({}),
  })
})

it('says the check failed, not that the headband is offline', async () => {
  eegHealth.mockResolvedValue({ refused: true, error: 'Too many requests. Slow down.' })
  render(<Adaptive />)

  expect(await screen.findByText('status unavailable')).toBeInTheDocument()
  // The word this replaces. "offline" is a claim about the headband, and a
  // refused probe has not earned it.
  expect(screen.queryByText('offline')).not.toBeInTheDocument()
})

it('goes on acting on the last answer without claiming it is current', async () => {
  // Two halves, and the first is why the state is kept stale at all: station
  // discovery and the Connect button are both gated on `available`, so
  // overwriting it with false during a refusal would take a working headband
  // off the page.
  //
  // The second is what the badge may say about that. "ready" is a claim that
  // the sidecar was reachable, and a refused probe has confirmed nothing -- so
  // the page keeps *behaving* as though the last answer holds while saying out
  // loud that it could not check. Asserting only "ready is still there" would
  // pass with both badges on screen at once, which is the one state this
  // change exists for and the one it must not contradict itself in.
  eegHealth.mockResolvedValueOnce({ available: true, ingest_mode: 'pull' })
  eegHealth.mockResolvedValue({ refused: true, error: 'Too many requests.' })
  render(<Adaptive />)

  await screen.findByText('ready')
  await screen.findByText('status unavailable', undefined, { timeout: 8000 })

  // Still acting on it: station discovery is gated on `available` and tears
  // itself down when that goes false, so calls continuing past the refusal is
  // the behaviour, not a badge asserting itself.
  const during = eegDevices.mock.calls.length
  await waitFor(() => expect(eegDevices.mock.calls.length).toBeGreaterThan(during),
                { timeout: 8000 })

  // And exactly one of the three badges is on screen.
  expect(screen.getByText('status unavailable')).toBeInTheDocument()
  expect(screen.queryByText('ready')).not.toBeInTheDocument()
  expect(screen.queryByText('offline')).not.toBeInTheDocument()
}, 25000)

it('still reports a sidecar that genuinely did not answer', async () => {
  // The other half: this must not have turned every failure into "unknown".
  eegHealth.mockResolvedValue({ available: false, error: 'Failed to fetch' })
  render(<Adaptive />)

  expect(await screen.findByText('offline')).toBeInTheDocument()
  expect(screen.queryByText('status unavailable')).not.toBeInTheDocument()
})
