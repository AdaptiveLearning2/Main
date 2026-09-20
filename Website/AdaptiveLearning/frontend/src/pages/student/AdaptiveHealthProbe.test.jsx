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
import { eegHealth } from '../../lib/signals'
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

it('keeps the last answer the probe did give', async () => {
  // The refusal carries no answer, so the most recent one that did is the best
  // thing known -- and it is what decides whether Connect is offered at all.
  // Overwriting it with false would take a working headband off the page.
  eegHealth.mockResolvedValueOnce({ available: true, ingest_mode: 'pull' })
  eegHealth.mockResolvedValue({ refused: true, error: 'Too many requests.' })
  render(<Adaptive />)

  await screen.findByText('ready')
  await waitFor(() => expect(eegHealth.mock.calls.length).toBeGreaterThan(1), { timeout: 8000 })

  expect(screen.getByText('ready')).toBeInTheDocument()
  expect(screen.queryByText('offline')).not.toBeInTheDocument()
}, 15000)

it('still reports a sidecar that genuinely did not answer', async () => {
  // The other half: this must not have turned every failure into "unknown".
  eegHealth.mockResolvedValue({ available: false, error: 'Failed to fetch' })
  render(<Adaptive />)

  expect(await screen.findByText('offline')).toBeInTheDocument()
  expect(screen.queryByText('status unavailable')).not.toBeInTheDocument()
})
