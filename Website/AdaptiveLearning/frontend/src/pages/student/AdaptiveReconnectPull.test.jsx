/**
 * The drop is announced under `pull` too -- the default mode.
 *
 * `AdaptiveReconnect.test.jsx` covers push, where the 3s status poll never
 * touches `connected`. Under pull it does, and a first version read the
 * bridge's `muse_connected` there as well: being the faster poll it set
 * `connected: false` before the 5s telemetry poll could claim the drop, and
 * that flip tore the telemetry effect down -- so nothing was toasted, no
 * `reconnecting` phase was entered, and the button did not come back either.
 * Whether a harness sees it depends on which poll observes the drop first;
 * see the timing note in the test. Checked by mutation: with the clause
 * restored this test fails, and an earlier version of it did not.
 */
import { it, expect, beforeEach, afterEach, vi } from 'vitest'
import { render, screen, cleanup, fireEvent, waitFor } from '@testing-library/react'

vi.mock('../../lib/api', async () => await import('../../test/mocks/apiFetch'))
vi.mock('../../lib/supabase', async () => await import('../../test/mocks/supabase'))
vi.mock('sonner', () => ({
  toast: { error: vi.fn(), success: vi.fn(), warning: vi.fn() },
}))
vi.mock('../../lib/session', () => ({
  endSession: vi.fn(async () => true),
  recordAnswer: vi.fn(async () => null),
}))

// The bridge as the backend relays it under pull, via /api/eeg/status.
// `stamps` records when each status read happened: the two polls are
// phase-locked to the first one, and the test times the drop off it.
const bridge = { ingestion: {}, stamps: [] }
vi.mock('../../lib/signals', () => ({
  createSignalRecorder: () => ({ start: vi.fn(async () => ({ ok: true })), stop: vi.fn() }),
  eegHealth: vi.fn(async () => ({ available: true, ingest_mode: 'pull' })),
  eegStatus: vi.fn(async () => {
    bridge.stamps.push(Date.now())
    return {
      ingest_mode: 'pull', service: true,
      poller: { running: true, samples: 3 },
      muse: { available: true, running: true, ingestion: { ...bridge.ingestion } },
    }
  }),
  eegDevices: vi.fn(async () => ({ devices: [{ device_id: 'default', kind: 'muse' }] })),
}))
vi.mock('../../lib/sidecar', () => ({
  startPush: vi.fn(), stopPush: vi.fn(), stopPushOnUnload: vi.fn(),
  pushStatus: vi.fn(async () => ({})), deviceStart: vi.fn(), deviceStop: vi.fn(),
  museRefresh: vi.fn(), museConnect: vi.fn(), museDisconnect: vi.fn(),
  museState: vi.fn(async () => ({})), devices: vi.fn(async () => []),
  releasePushIfIdle: vi.fn(async () => ({ stopped: true, devices: [] })),
  sidecarDebug: vi.fn(async () => ({})),
}))
vi.mock('../../context/AuthContext', () => ({
  useAuth: () => ({ user: { id: 'u1', email: 'a@b.c' }, role: 'student', loading: false }),
}))

import { toast } from 'sonner'
import { apiFetch, mockApi, resetApi } from '../../test/mocks/apiFetch'
import Adaptive from './Adaptive'

const CONNECTED = { muse_connected: true, muse_devices: ['Muse-1'], battery_percent: 80,
                    auto_reconnect: true, reconnecting: false, reconnect_attempt: 0,
                    reconnect_max_attempts: 5, reconnect_exhausted: false }

beforeEach(() => {
  resetApi()
  vi.clearAllMocks()
  bridge.ingestion = { ...CONNECTED }
  bridge.stamps = []
  mockApi({
    'GET /api/profile/me': () => ({ id: 'u1', role: 'student', grade_level: '4th Grade' }),
    'GET /api/classes': () => [],
    'GET /api/performance/student/u1': () => [],
    'POST /api/sessions/start': () => ({ id: 'sess-1' }),
    'POST /api/eeg/muse/disconnect': () => ({ ok: true }),
    'POST /api/eeg/muse/refresh': () => ({ ok: true }),
    'POST /api/eeg/muse/connect': () => ({ ok: true }),
  })
})

afterEach(() => cleanup())

it('announces a drop under pull, where the poller keeps running through it', async () => {
  render(<Adaptive />)
  const button = await screen.findByRole('button', { name: /connect headband/i })
  await waitFor(() => expect(button).not.toBeDisabled())
  fireEvent.click(button)
  // Under pull `connected` means the backend's poller is running, which is
  // true from `rec.start()` -- before the scan and connect have finished. Wait
  // for the pairing itself to complete, or its last poll would report the
  // restored link as a first connection rather than a recovery.
  await screen.findByText(/STREAMING/, {}, { timeout: 10000 })
  await waitFor(() => expect(apiFetch.mock.calls.some(c => c[0] === '/api/eeg/muse/connect')).toBe(true),
                { timeout: 10000 })

  // *When* the drop happens decides whether this test can see the bug. Both
  // polls start from the first status read: the 3s one ticks at 3, 6, 9...
  // and the 5s one at 5, 10... The bug -- the status poll setting
  // `connected: false` and tearing the telemetry effect down -- only shows
  // when the status poll observes the drop first, so the drop has to land in
  // a window where the next 3s tick comes before the next 5s tick. Dropping
  // at 5.3s puts the first observation at 6 (status) and the next at 10
  // (telemetry). A first version dropped at ~4.7s, where telemetry at 5
  // wins, and passed against the bug it was written for.
  //
  // Keyed to the tick, not to the clock: the drop goes in right after the
  // 5s read has *happened*, which is a recorded event. An absolute offset
  // sat 278ms clear of that tick and drifted out of the window under load,
  // in both directions, without failing.
  const t0 = bridge.stamps[0]
  await waitFor(() => expect(bridge.stamps.some(t => t >= t0 + 4900)).toBe(true),
                { timeout: 8000 })

  bridge.ingestion = { ...CONNECTED, muse_connected: false, reconnecting: true,
                       reconnect_attempt: 1, battery_percent: null }
  await screen.findByText(/reconnecting \(attempt 1 of 5\)/, {}, { timeout: 9000 })
  expect(toast.warning).toHaveBeenCalledWith('The headband disconnected.', expect.anything())
  expect(screen.getByRole('button', { name: /stop trying/i })).toBeInTheDocument()
  // The page waited for the bridge rather than starting a scan of its own.
  expect(apiFetch.mock.calls.filter(c => c[0] === '/api/eeg/muse/refresh')).toHaveLength(1)

  bridge.ingestion = { ...CONNECTED }
  await screen.findByText(/STREAMING/, {}, { timeout: 6000 })
  expect(toast.success).toHaveBeenCalledWith('Headband reconnected.')
}, 60_000)
