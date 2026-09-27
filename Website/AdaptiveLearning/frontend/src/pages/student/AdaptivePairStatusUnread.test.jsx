/** An unlanded status read during pairing is not an empty answer: no teardown, no blaming the hardware. */
import { it, expect, beforeEach, vi } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'

vi.mock('../../lib/api', async () => await import('../../test/mocks/apiFetch'))
vi.mock('../../lib/supabase', async () => await import('../../test/mocks/supabase'))
vi.mock('sonner', () => ({ toast: { error: vi.fn(), success: vi.fn(), warning: vi.fn() } }))
vi.mock('../../lib/session', () => ({
  endSession: vi.fn(async () => true),
  markEegStarted: vi.fn(async () => true),
  recordAnswer: vi.fn(async () => ({ topic: 'ordering' })),
}))
vi.mock('../../lib/signals', () => ({
  // `start` must resolve running, or the click throws before the adoption check.
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
    // Routed so the teardown can happen, rather than passing on a crash.
    'POST /api/eeg/muse/disconnect': () => ({ ok: true }),
    'POST /api/eeg/muse/refresh': () => ({ ok: true }),
    'POST /api/eeg/muse/connect': () => ({ ok: true }),
  })
})

it('leaves a live link alone when the status read did not land', async () => {
  // The swallowed fallback carries no `muse`.
  eegStatus.mockResolvedValue({ answered: false, service: false, poller: { running: false } })
  render(<Adaptive />)

  const button = await screen.findByRole('button', { name: /connect headband/i })
  await waitFor(() => expect(button).not.toBeDisabled())
  fireEvent.click(button)

  // The wait outlasts the unguarded path (1.5 s settle + 12 s scan).
  await waitFor(() => expect(toast.error).toHaveBeenCalled(), { timeout: 20000 })
  expect(called('/api/eeg/muse/disconnect')).toBe(false)
  expect(called('/api/eeg/muse/refresh')).toBe(false)

  // The message names the check, not the hardware.
  const [title, opts] = toast.error.mock.calls.at(-1)
  expect(`${title} ${opts?.description ?? ''}`).toMatch(/EEG service/)
  expect(`${title} ${opts?.description ?? ''}`).not.toMatch(/power|Bluetooth|No headband found/i)
}, 30000)

const UNLANDED = { answered: false, service: false, poller: { running: false } }

it('does not report an empty scan from twelve reads that never landed', async () => {
  // The first read lands (scan is correct); unlanded scan reads must not become `no_device`.
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
  // 60 s for ~14 s of real-timer waiting: headroom for a loaded full run.
}, 60000)

it('does not blame the firmware for a connect poll that never landed', async () => {
  // Unlanded connect-poll reads must not become `not_connected`'s power-cycle advice.
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
