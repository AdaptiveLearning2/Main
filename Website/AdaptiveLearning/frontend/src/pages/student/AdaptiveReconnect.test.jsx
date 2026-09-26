/** A headband drop is announced and watched, not reset; real timers, since every fake-clock version hung. */
import { it, expect, beforeEach, afterEach, vi } from 'vitest'
import { render, screen, cleanup, fireEvent, waitFor } from '@testing-library/react'

vi.mock('../../lib/api', async () => await import('../../test/mocks/apiFetch'))
vi.mock('../../lib/supabase', async () => await import('../../test/mocks/supabase'))
vi.mock('sonner', () => ({
  toast: { error: vi.fn(), success: vi.fn(), warning: vi.fn() },
}))
vi.mock('../../lib/session', () => ({
  endSession: vi.fn(async () => true),
  markEegStarted: vi.fn(async () => true),
  recordAnswer: vi.fn(async () => null),
}))
vi.mock('../../lib/signals', () => ({
  createSignalRecorder: () => ({ start: vi.fn(), stop: vi.fn() }),
  // Push mode: the backend never probes a sidecar it has no route to.
  eegHealth: vi.fn(async () => ({ available: null, ingest_mode: 'push' })),
  eegStatus: vi.fn(async () => ({ ingest_mode: 'push', service: null, poller: {} })),
  eegDevices: vi.fn(async () => ({ devices: [] })),
}))

// The bridge as the sidecar reports it. Tests rewrite `bridge` to move it.
const bridge = { ingestion: {} }
vi.mock('../../lib/sidecar', () => ({
  startPush: vi.fn(async () => ({})), stopPush: vi.fn(async () => ({})),
  stopPushOnUnload: vi.fn(),
  pushStatus: vi.fn(async () => ({ enabled: true, running: true, recorded: {} })),
  deviceStart: vi.fn(async () => ({})), deviceStop: vi.fn(async () => ({})),
  museRefresh: vi.fn(async () => ({})),
  // Not connected until asked; a harness that starts connected is adopted without a scan.
  museConnect: vi.fn(async () => { bridge.ingestion = { ...bridge.ingestion, muse_connected: true }; return {} }),
  museDisconnect: vi.fn(async () => ({})),
  museState: vi.fn(async () => ({ running: true, ingestion: { ...bridge.ingestion } })),
  devices: vi.fn(async () => [{ device_id: 'default', kind: 'muse', running: false }]),
  releasePushIfIdle: vi.fn(async () => ({ stopped: true, devices: [] })),
  sidecarDebug: vi.fn(async () => ({})),
}))
vi.mock('../../context/AuthContext', () => ({
  useAuth: () => ({ user: { id: 'u1', email: 'a@b.c' }, role: 'student', loading: false }),
}))

import { toast } from 'sonner'
import { museRefresh, museConnect, museDisconnect, museState, deviceStart, startPush } from '../../lib/sidecar'
import { markEegStarted } from '../../lib/session'
import { mockApi, overrideApi, resetApi } from '../../test/mocks/apiFetch'
import Adaptive from './Adaptive'

// `eeg_age_ms` is required for "connected": a link counts only with EEG flowing.
const CONNECTED = { muse_connected: true, muse_devices: ['Muse-1'], battery_percent: 80,
                    auto_reconnect: true, reconnecting: false, reconnect_attempt: 0,
                    reconnect_max_attempts: 5, reconnect_exhausted: false, eeg_age_ms: 2 }

// The drop is noticed by a 5s poll; this is that plus slack.
const POLL = { timeout: 8000 }
const TEST_TIMEOUT = 60_000

beforeEach(() => {
  resetApi()
  vi.clearAllMocks()
  // Discoverable, not yet connected; `museConnect` flips it.
  bridge.ingestion = { ...CONNECTED, muse_connected: false }
  mockApi({
    'GET /api/profile/me': () => ({ id: 'u1', role: 'student', grade_level: '4th Grade' }),
    'GET /api/classes': () => [],
    'GET /api/performance/student/u1': () => [],
  })
})

afterEach(() => cleanup())

const sleep = (ms) => new Promise(r => setTimeout(r, ms))

/** Click Connect and walk the scan/connect sequence through to connected. */
async function connect() {
  render(<Adaptive />)
  const button = await screen.findByRole('button', { name: /connect headband/i })
  // Enabled once the health check has said "push" and a station is known.
  await waitFor(() => expect(button).not.toBeDisabled())
  fireEvent.click(button)
  // disconnect -> 1.5s settle -> scan -> 1s poll -> connect -> 1s poll.
  await screen.findByText(/STREAMING/, {}, { timeout: 10000 })
}

it('adopts a link the bridge already has instead of tearing it down to scan for it', async () => {
  // Disconnect-then-scan would drop a link the bridge already holds.
  bridge.ingestion = { ...CONNECTED, active_muse_name: 'Muse-1', eeg_age_ms: 4 }
  render(<Adaptive />)
  const button = await screen.findByRole('button', { name: /connect headband/i })
  await waitFor(() => expect(button).not.toBeDisabled())
  fireEvent.click(button)
  await screen.findByText(/STREAMING/, {}, { timeout: 5000 })
  expect(museDisconnect).not.toHaveBeenCalled()
  expect(museRefresh).not.toHaveBeenCalled()
  expect(museConnect).not.toHaveBeenCalled()
  expect(screen.getByRole('button', { name: /disconnect/i })).toBeInTheDocument()
}, TEST_TIMEOUT)

it('does not let the reconnect loop declare a silent link recovered', async () => {
  // "Connected" with stale EEG is not a recovery; the loop scans instead.
  await connect()
  bridge.ingestion = { ...CONNECTED, muse_connected: false, reconnecting: false,
                       reconnect_exhausted: true, battery_percent: null }
  await screen.findByText(/The headband disconnected/, {}, POLL)
  // Inside the loop's first 2s backoff: connected, but no EEG for 20s.
  bridge.ingestion = { ...CONNECTED, reconnect_exhausted: true, eeg_age_ms: 20_000 }
  const refreshes = museRefresh.mock.calls.length
  await waitFor(() => expect(museRefresh.mock.calls.length).toBeGreaterThan(refreshes),
                { timeout: 8000 })
  // The scan ran, so the "came back on its own" shortcut did not.
  expect(toast.success).not.toHaveBeenCalled()
}, TEST_TIMEOUT)

it('does not call a drop recovered until EEG is flowing again', async () => {
  // The telemetry poll's recovery follows the same rule: EEG arriving ends it.
  await connect()
  bridge.ingestion = { ...CONNECTED, muse_connected: false, reconnecting: true,
                       reconnect_attempt: 1, battery_percent: null }
  await screen.findByText(/reconnecting \(attempt 1 of 5\)/, {}, POLL)

  bridge.ingestion = { ...CONNECTED, reconnecting: true, reconnect_attempt: 1, eeg_age_ms: 20_000 }
  await sleep(5000)
  expect(screen.getByText(/reconnecting \(attempt 1 of 5\)/)).toBeInTheDocument()
  expect(toast.success).not.toHaveBeenCalled()

  bridge.ingestion = { ...CONNECTED }
  await screen.findByText(/STREAMING/, {}, { timeout: 5000 })
  expect(toast.success).toHaveBeenCalledWith('Headband reconnected.')
}, TEST_TIMEOUT)

it('lets a bridge reconnect settle instead of tearing it down for having no packet yet', async () => {
  // The bridge reports no age until the first packet after CONNECTED; null age is left to settle.
  await connect()
  bridge.ingestion = { ...CONNECTED, muse_connected: false, reconnecting: true,
                       reconnect_attempt: 1, battery_percent: null }
  await screen.findByText(/reconnecting \(attempt 1 of 5\)/, {}, POLL)

  // The bridge's attempt landed: connected, its retries over, no EEG yet.
  bridge.ingestion = { ...CONNECTED, reconnecting: false, eeg_age_ms: null }
  await sleep(5000)
  expect(museDisconnect).toHaveBeenCalledTimes(1)   // only the original pairing
  expect(museRefresh).toHaveBeenCalledTimes(1)
  expect(toast.success).not.toHaveBeenCalled()
  // Tells this guard apart from the loop's own grace: the page's "of 3" loop must not have started.
  expect(screen.queryByText(/of 3\)/)).toBeNull()

  bridge.ingestion = { ...CONNECTED }
  await screen.findByText(/STREAMING/, {}, { timeout: 5000 })
  expect(toast.success).toHaveBeenCalledWith('Headband reconnected.')
  expect(museRefresh).toHaveBeenCalledTimes(1)
}, TEST_TIMEOUT)

it('lets the page-driven loop wait for a settling link too, instead of scanning over it', async () => {
  // Same rule on the page-driven loop: wait, then adopt once EEG flows.
  await connect()
  bridge.ingestion = { ...CONNECTED, muse_connected: false, reconnecting: false,
                       reconnect_exhausted: true, muse_devices: [] }
  await screen.findByText(/reconnecting \(attempt 1 of 3\)/, {}, POLL)
  // Inside the loop's 2s backoff the bridge connects, no packet yet.
  bridge.ingestion = { ...CONNECTED, reconnect_exhausted: true, eeg_age_ms: null }
  await sleep(6000)
  expect(museRefresh).toHaveBeenCalledTimes(1)      // only the original pairing
  expect(museDisconnect).toHaveBeenCalledTimes(1)
  bridge.ingestion = { ...CONNECTED }
  await screen.findByText(/STREAMING/, {}, { timeout: 5000 })
  expect(museRefresh).toHaveBeenCalledTimes(1)
}, TEST_TIMEOUT)

it('gives a settling link a bounded grace, then treats it as dead', async () => {
  await connect()
  bridge.ingestion = { ...CONNECTED, muse_connected: false, reconnecting: true,
                       reconnect_attempt: 1, battery_percent: null }
  await screen.findByText(/reconnecting \(attempt 1 of 5\)/, {}, POLL)
  bridge.ingestion = { ...CONNECTED, reconnecting: false, eeg_age_ms: null }
  // Past the grace the page scans (the bridge-watchdog-off case).
  await waitFor(() => expect(museRefresh).toHaveBeenCalledTimes(2), { timeout: 20000 })
  await screen.findByText(/STREAMING/, {}, { timeout: 10000 })

  // The grace is per episode: a spent one must not carry into the next drop.
  bridge.ingestion = { ...CONNECTED }
  await sleep(1000)
  bridge.ingestion = { ...CONNECTED, muse_connected: false, reconnecting: true,
                       reconnect_attempt: 1, battery_percent: null }
  await screen.findByText(/reconnecting \(attempt 1 of 5\)/, {}, POLL)
  // The bridge's 2s backoff matches the poll, so the page first sees a settling link.
  bridge.ingestion = { ...CONNECTED, reconnecting: false, eeg_age_ms: null }
  await sleep(5000)
  expect(museRefresh).toHaveBeenCalledTimes(2)   // no third scan inside the fresh grace
  bridge.ingestion = { ...CONNECTED }
  await screen.findByText(/STREAMING/, {}, { timeout: 5000 })
}, TEST_TIMEOUT)

it('does not adopt a link the bridge calls connected but has no recent EEG from', async () => {
  // libMuse keeps saying CONNECTED after EEG stops; a stale or absent age goes through disconnect-then-scan.
  bridge.ingestion = { ...CONNECTED, active_muse_name: 'Muse-1', eeg_age_ms: 20_000 }
  render(<Adaptive />)
  const button = await screen.findByRole('button', { name: /connect headband/i })
  await waitFor(() => expect(button).not.toBeDisabled())
  fireEvent.click(button)
  await waitFor(() => expect(museDisconnect).toHaveBeenCalled(), { timeout: 5000 })
  await waitFor(() => expect(museRefresh).toHaveBeenCalled(), { timeout: 5000 })
}, TEST_TIMEOUT)

it('announces a drop and shows the bridge reconnecting instead of resetting the panel', async () => {
  await connect()
  const refreshes = museRefresh.mock.calls.length

  bridge.ingestion = { ...CONNECTED, muse_connected: false, reconnecting: true,
                       reconnect_attempt: 2, battery_percent: null }
  await screen.findByText(/reconnecting \(attempt 2 of 5\)/, {}, POLL)

  expect(toast.warning).toHaveBeenCalledWith('The headband disconnected.', expect.anything())
  expect(screen.getByRole('button', { name: /stop trying/i })).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: /connect headband/i })).not.toBeInTheDocument()
  // No page scan while the bridge is trying.
  expect(museRefresh.mock.calls.length).toBe(refreshes)

  bridge.ingestion = { ...CONNECTED }
  await screen.findByText(/STREAMING/, {}, { timeout: 5000 })
  expect(toast.success).toHaveBeenCalledWith('Headband reconnected.')
}, TEST_TIMEOUT)

it('says a flapping link dropped once, not once per drop', async () => {
  // Edge of BLE range: the panel tracks every drop; the toasts must not.
  await connect()
  const drop = { ...CONNECTED, muse_connected: false, reconnecting: true,
                 reconnect_attempt: 1, battery_percent: null }

  bridge.ingestion = drop
  await screen.findByText(/reconnecting \(attempt 1 of 5\)/, {}, POLL)
  bridge.ingestion = { ...CONNECTED }
  await screen.findByText(/STREAMING/, {}, { timeout: 5000 })
  bridge.ingestion = drop
  await screen.findByText(/reconnecting \(attempt 1 of 5\)/, {}, POLL)
  bridge.ingestion = { ...CONNECTED }
  await screen.findByText(/STREAMING/, {}, { timeout: 5000 })

  expect(toast.warning).toHaveBeenCalledTimes(1)
  expect(toast.success).toHaveBeenCalledTimes(1)
}, TEST_TIMEOUT)

it('announces a new episode after a teardown, even inside the throttle window', async () => {
  // The throttle is once per episode, not per minute; a hand re-pair starts a new one.
  await connect()
  bridge.ingestion = { ...CONNECTED, muse_connected: false, reconnecting: true,
                       reconnect_attempt: 1, battery_percent: null }
  await screen.findByText(/The headband disconnected/, {}, POLL)
  expect(toast.warning).toHaveBeenCalledTimes(1)

  fireEvent.click(screen.getByRole('button', { name: /stop trying/i }))
  const button = await screen.findByRole('button', { name: /connect headband/i })
  bridge.ingestion = { ...CONNECTED }
  await waitFor(() => expect(button).not.toBeDisabled())
  fireEvent.click(button)
  await screen.findByText(/STREAMING/, {}, { timeout: 10000 })

  bridge.ingestion = { ...CONNECTED, muse_connected: false, reconnecting: true,
                       reconnect_attempt: 1, battery_percent: null }
  await screen.findByText(/reconnecting \(attempt 1 of 5\)/, {}, POLL)
  expect(toast.warning).toHaveBeenCalledTimes(2)
  bridge.ingestion = { ...CONNECTED }
  await screen.findByText(/STREAMING/, {}, { timeout: 5000 })
  expect(toast.success).toHaveBeenCalledTimes(1)
}, TEST_TIMEOUT)

it('takes over once the bridge has given up, and gives the student a way out', async () => {
  await connect()
  const refreshes = museRefresh.mock.calls.length

  bridge.ingestion = { ...CONNECTED, muse_connected: false, reconnecting: false,
                       reconnect_exhausted: true, muse_devices: [] }
  await screen.findByText(/The headband disconnected/, {}, POLL)

  // First page-driven attempt: 2s backoff, then a scan that finds nothing.
  await waitFor(() => expect(museRefresh.mock.calls.length).toBeGreaterThan(refreshes),
                { timeout: 8000 })
  expect(museConnect).toHaveBeenCalledTimes(1) // only the original pairing

  // The way out stays on screen: the page-driven scan never disables it.
  const disconnects = museDisconnect.mock.calls.length
  fireEvent.click(screen.getByRole('button', { name: /stop trying/i }))
  await screen.findByRole('button', { name: /connect headband/i })
  // "Stop trying" tears down like Disconnect, which also cancels the bridge.
  expect(museDisconnect.mock.calls.length).toBeGreaterThan(disconnects)

  // The cancelled scan must not pair the headband once it turns up.
  bridge.ingestion = { ...CONNECTED }
  await sleep(2500)
  expect(museConnect).toHaveBeenCalledTimes(1)
}, TEST_TIMEOUT)

it('stops the page-driven loop when the page unmounts, and sends the shared bridge nothing more', async () => {
  // The loop has its own cancel token, and each attempt's disconnect hits the shared bridge.
  await connect()
  bridge.ingestion = { ...CONNECTED, muse_connected: false, reconnecting: false,
                       reconnect_exhausted: true, muse_devices: [] }
  await screen.findByText(/The headband disconnected/, {}, POLL)

  // Inside the first 2s backoff, before any attempt has run.
  cleanup()
  // Counted after unmount: the effects' polls stop, so any later read is the loop's.
  const disconnects = museDisconnect.mock.calls.length
  const refreshes = museRefresh.mock.calls.length
  const reads = museState.mock.calls.length
  // Past all three backoffs (2s + 4s + 8s).
  await sleep(16000)
  expect(museState.mock.calls.length).toBe(reads)
  expect(museDisconnect.mock.calls.length).toBe(disconnects)
  expect(museRefresh.mock.calls.length).toBe(refreshes)
  expect(toast.error).not.toHaveBeenCalled()
}, TEST_TIMEOUT)

it('sends no disconnect for a Connect the page left before the pairing began', async () => {
  // Two awaits precede `pairOnce`, whose first step is a global bridge disconnect.
  deviceStart.mockImplementationOnce(() => new Promise(r => setTimeout(() => r({}), 800)))
  render(<Adaptive />)
  const button = await screen.findByRole('button', { name: /connect headband/i })
  await waitFor(() => expect(button).not.toBeDisabled())
  fireEvent.click(button)
  await sleep(300)
  cleanup()
  await sleep(2500)
  expect(museDisconnect).not.toHaveBeenCalled()
  expect(museRefresh).not.toHaveBeenCalled()
}, TEST_TIMEOUT)

it('drives the reconnect itself against a bridge too old to report one', async () => {
  await connect()

  // No `reconnecting` field at all: an older native_bridge build.
  bridge.ingestion = { muse_connected: false, muse_devices: ['Muse-1'], battery_percent: null }
  await screen.findByText(/reconnecting \(attempt 1 of 3\)/, {}, POLL)

  // The link comes back during the backoff; the loop notices before scanning.
  bridge.ingestion = { ...CONNECTED }
  await screen.findByText(/STREAMING/, {}, { timeout: 6000 })
  expect(toast.success).toHaveBeenCalledWith('Headband reconnected.')
}, TEST_TIMEOUT)

it('shows a contact hint only after two poor readings in a row, and clears it on one good one', async () => {
  await connect()
  const hint = /Adjust the headband/

  // Anchored on reads served, not elapsed time: a whole poll of slack.
  const readsBefore = museState.mock.calls.length
  bridge.ingestion = { ...CONNECTED, hsi: [4, 4, 4, 4], is_good: [0, 0, 0, 0] }
  await waitFor(() => expect(museState.mock.calls.length).toBe(readsBefore + 1), POLL)
  await sleep(250)
  expect(screen.queryByText(hint)).not.toBeInTheDocument()

  // The second consecutive poor reading is what raises it.
  await screen.findByText(hint, {}, POLL)

  bridge.ingestion = { ...CONNECTED, hsi: [1, 1, 1, 1], is_good: [1, 1, 1, 1] }
  await waitFor(() => expect(screen.queryByText(hint)).not.toBeInTheDocument(), POLL)
}, TEST_TIMEOUT)


// ── the push EEG start the backend cannot see ───────────────────────────────

function withQuestions() {
  overrideApi('/api/sessions/start', () => ({ id: 'sess-push' }), 'POST')
  overrideApi(/^\/api\/generate-question\?/, () => ({
    id: 'q1', question_text: 'What is 2 + 2?', question_topic: 'ordering',
    answer_options: ['3', '4', '5'], correct_answer: '4', difficulty: 'easy',
  }))
}

it('reports the EEG start once a headband streams into the handed-over session', async () => {
  // Under push the backend never sees the start, and its signals_missing alert needs it.
  withQuestions()
  await connect()
  fireEvent.click(screen.getByRole('button', { name: /generate question/i }))
  await screen.findByText('What is 2 + 2?')

  await waitFor(() => expect(markEegStarted).toHaveBeenCalledWith('sess-push'), POLL)
  expect(markEegStarted).toHaveBeenCalledTimes(1)
}, TEST_TIMEOUT)

it('reports nothing for a session with no headband streaming', async () => {
  // A camera-only push session would otherwise raise signals_missing for EEG it never had.
  withQuestions()
  render(<Adaptive />)
  fireEvent.click(await screen.findByRole('button', { name: /generate question/i }))
  await screen.findByText('What is 2 + 2?')
  await waitFor(() => expect(startPush).toHaveBeenCalledWith('sess-push'))
  await sleep(500)

  expect(markEegStarted).not.toHaveBeenCalled()
}, TEST_TIMEOUT)
