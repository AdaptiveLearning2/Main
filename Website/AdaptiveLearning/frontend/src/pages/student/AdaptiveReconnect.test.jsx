/**
 * A headband that drops mid-session is said out loud and watched, not
 * silently reset.
 *
 * The status poll used to answer `muse_connected === false` by putting the
 * panel back to "Connect Headband" as if nothing had been paired -- no toast,
 * and the poll itself ended with `connected`, so nothing was left watching
 * for the link to come back. The native bridge now retries a dropped link
 * itself and reports progress; this page shows that progress, and only
 * drives its own scan+connect once the bridge has given up.
 *
 * Real timers, deliberately. The pairing sequence is a chain of 1-1.5s waits
 * and the drop is noticed by a 5s poll, and every attempt to drive that from
 * a fake clock left React's own scheduling stuck behind it. So each test
 * costs ten to twenty real seconds; the timeouts below say so.
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
  // The bridge as it behaves: not connected until asked. A harness that
  // starts connected is adopted by the page without a scan, which is the
  // right behaviour and the wrong fixture for tests about the scan.
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
import { museRefresh, museConnect, museDisconnect, museState, deviceStart } from '../../lib/sidecar'
import { mockApi, resetApi } from '../../test/mocks/apiFetch'
import Adaptive from './Adaptive'

const CONNECTED = { muse_connected: true, muse_devices: ['Muse-1'], battery_percent: 80,
                    auto_reconnect: true, reconnecting: false, reconnect_attempt: 0,
                    reconnect_max_attempts: 5, reconnect_exhausted: false }

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
  // begin -> disconnect -> 1.5s settle -> scan -> 1s poll (devices) ->
  // connect -> 1s poll (muse_connected).
  await screen.findByText(/STREAMING/, {}, { timeout: 10000 })
}

it('adopts a link the bridge already has instead of tearing it down to scan for it', async () => {
  // Seen on hardware: after both the bridge and the page had given up, the
  // headband was switched back on and the bridge had it connected before
  // Connect was clicked. The click's disconnect-then-scan dropped that link
  // 1.5s in -- "connects, then immediately disconnects".
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

it('does not adopt a link the bridge calls connected but has no recent EEG from', async () => {
  // libMuse keeps saying CONNECTED after EEG stops. Adopting that would put
  // the panel on STREAMING with nothing flowing and skip the one bridge
  // disconnect a click can still send, so a dead link could never be
  // cleared. A stale age -- or no age at all, from an older bridge -- goes
  // through the disconnect-then-scan as before.
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
  // The panel must not have gone back to the never-paired state.
  expect(screen.queryByRole('button', { name: /connect headband/i })).not.toBeInTheDocument()
  // And the page did not start its own scan while the bridge was trying.
  expect(museRefresh.mock.calls.length).toBe(refreshes)

  bridge.ingestion = { ...CONNECTED }
  await screen.findByText(/STREAMING/, {}, { timeout: 5000 })
  expect(toast.success).toHaveBeenCalledWith('Headband reconnected.')
}, TEST_TIMEOUT)

it('says a flapping link dropped once, not once per drop', async () => {
  // Measured at the edge of BLE range: reconnect in seconds, drop eight
  // later, repeat. The panel tracks every drop; the toasts must not.
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
  // The throttle is once per *episode*, not once per minute. A student who
  // stopped trying and re-paired by hand has started a new one, and a drop
  // on that link is news again -- keyed to wall-clock alone, it was not.
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

  // First page-driven attempt: a 2s backoff, then a scan. No headband is
  // found, so it will go on to the next attempt rather than succeed.
  await waitFor(() => expect(museRefresh.mock.calls.length).toBeGreaterThan(refreshes),
                { timeout: 8000 })
  expect(museConnect).toHaveBeenCalledTimes(1) // only the original pairing

  // The way out is on screen for the whole attempt -- the page-driven scan
  // does not step the panel through scanning/connecting, where the button
  // is disabled.
  const disconnects = museDisconnect.mock.calls.length
  fireEvent.click(screen.getByRole('button', { name: /stop trying/i }))
  await screen.findByRole('button', { name: /connect headband/i })
  // "Stop trying" tears down like Disconnect, which also cancels whatever
  // the bridge might still be doing -- every command it receives does.
  expect(museDisconnect.mock.calls.length).toBeGreaterThan(disconnects)

  // And the cancelled scan must not go on to pair the headband behind the
  // student's back once it turns up.
  bridge.ingestion = { ...CONNECTED }
  await sleep(2500)
  expect(museConnect).toHaveBeenCalledTimes(1)
}, TEST_TIMEOUT)

it('stops the page-driven loop when the page unmounts, and sends the shared bridge nothing more', async () => {
  // Cancelling `pairOnce` alone was half of it: the loop around it had its
  // own token, which an unmount never set, and each attempt's disconnect
  // goes to the one shared bridge device. Measured before the fix: three
  // disconnects and three status reads over ~32s after unmount, then a
  // failure toast on whatever page the student was on by then.
  await connect()
  bridge.ingestion = { ...CONNECTED, muse_connected: false, reconnecting: false,
                       reconnect_exhausted: true, muse_devices: [] }
  await screen.findByText(/The headband disconnected/, {}, POLL)

  // Inside the first 2s backoff, before any attempt has run.
  cleanup()
  // Counted after the unmount: the effects' own polls stop with it, so any
  // later read is the loop's. Each attempt reads the bridge before pairing,
  // so this is what catches a loop that survives even when its disconnect
  // is blocked further down.
  const disconnects = museDisconnect.mock.calls.length
  const refreshes = museRefresh.mock.calls.length
  const reads = museState.mock.calls.length
  // Past all three backoffs (2s + 4s + 8s), where the loop would give up
  // and toast.
  await sleep(16000)
  expect(museState.mock.calls.length).toBe(reads)
  expect(museDisconnect.mock.calls.length).toBe(disconnects)
  expect(museRefresh.mock.calls.length).toBe(refreshes)
  expect(toast.error).not.toHaveBeenCalled()
}, TEST_TIMEOUT)

it('sends no disconnect for a Connect the page left before the pairing began', async () => {
  // Connect's own path: two awaits (the session, then bringing the device
  // up) run before `pairOnce`, and its first step is a disconnect that is
  // global to the shared bridge. Leaving during those awaits used to let
  // that disconnect land after the page was gone. The reconnect loop no
  // longer reaches `pairOnce` cancelled, so this is the one path the
  // pre-disconnect check still protects, and nothing else exercised it.
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

  bridge.ingestion = { ...CONNECTED, hsi: [4, 4, 4, 4], is_good: [0, 0, 0, 0] }
  // One poor frame is a head turn. The poll is 5s, so at 6s exactly one
  // poor reading has landed and nothing should show yet.
  await sleep(6000)
  expect(screen.queryByText(hint)).not.toBeInTheDocument()
  await screen.findByText(hint, {}, POLL)

  bridge.ingestion = { ...CONNECTED, hsi: [1, 1, 1, 1], is_good: [1, 1, 1, 1] }
  await waitFor(() => expect(screen.queryByText(hint)).not.toBeInTheDocument(), POLL)
}, TEST_TIMEOUT)
