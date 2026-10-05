/** Under push the page asks `GET /api/recording/me` before opening a sensor, and switches one off on a refusal. */
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
  eegHealth: vi.fn(async () => ({ available: null, ingest_mode: 'push' })),
  eegStatus: vi.fn(async () => ({ ingest_mode: 'push', service: null, poller: {} })),
  eegDevices: vi.fn(async () => ({ devices: [] })),
}))
// Short, so a withdrawal lands inside a test; the real interval is pinned in AdaptiveHealthProbe.test.jsx.
vi.mock('./pollIntervals', async (importOriginal) => ({
  ...(await importOriginal()), PUSH_POLL_MS: 300,
}))

// The sidecar as each test sets it.
const rig = { cameraRunning: false, headbandRunning: false, ingestion: {} }
vi.mock('../../lib/sidecar', () => ({
  startPush: vi.fn(async () => ({})), stopPush: vi.fn(async () => ({})),
  stopPushOnUnload: vi.fn(),
  pushStatus: vi.fn(async () => ({ enabled: true, running: true, recorded: {} })),
  deviceStart: vi.fn(async () => ({})), deviceStop: vi.fn(async () => ({})),
  deviceStopOnUnload: vi.fn(),
  museRefresh: vi.fn(async () => ({})),
  museConnect: vi.fn(async () => { rig.ingestion = { ...rig.ingestion, muse_connected: true }; return {} }),
  museDisconnect: vi.fn(async () => ({})),
  museState: vi.fn(async () => ({ running: true, ingestion: { ...rig.ingestion } })),
  devices: vi.fn(async () => [
    { device_id: 'default', kind: 'muse', running: rig.headbandRunning },
    { device_id: 'camera', kind: 'face', running: rig.cameraRunning },
  ]),
  releasePushIfIdle: vi.fn(async () => ({ stopped: true, devices: [] })),
  sidecarDebug: vi.fn(async () => ({})),
}))
vi.mock('../../lib/pageRestore', () => ({ reloadIfRestored: vi.fn() }))
vi.mock('../../context/AuthContext', () => ({
  useAuth: () => ({ user: { id: 'u1', email: 'a@b.c' }, role: 'student', loading: false }),
}))

import { toast } from 'sonner'
import { deviceStart, deviceStop, museConnect, museDisconnect, museRefresh } from '../../lib/sidecar'
import { apiFetch, apiError, mockApi, resetApi } from '../../test/mocks/apiFetch'
import { buildRecordingPermits } from '../../test/fixtures/recordingPermits'
import Adaptive from './Adaptive'

// `eeg_age_ms` is required for "connected": a link counts only with EEG flowing.
const CONNECTED = { muse_connected: true, muse_devices: ['Muse-1'], battery_percent: 80,
                    auto_reconnect: true, reconnecting: false, reconnect_attempt: 0,
                    reconnect_max_attempts: 5, reconnect_exhausted: false, eeg_age_ms: 2 }
const HEADBAND_REFUSED = { eeg: 'declined', headband_optical: 'declined' }
const DECLINED_CAMERA = "Camera recording is off: a parent hasn't turned it on."
const DECLINED_HEADBAND = "Headband recording is off: a parent hasn't turned it on."
const TEST_TIMEOUT = 60_000

// What the route answers; tests reassign it, and each request reads it when made.
let answer

beforeEach(() => {
  resetApi()
  vi.clearAllMocks()
  rig.cameraRunning = false
  rig.headbandRunning = false
  rig.ingestion = { ...CONNECTED, muse_connected: false }
  answer = () => buildRecordingPermits()
  mockApi({
    'GET /api/profile/me': () => ({ id: 'u1', role: 'student', grade_level: '4th Grade' }),
    'GET /api/recording/me': () => answer(),
    'GET /api/classes': () => [],
    'GET /api/performance/student/u1': () => [],
  })
})

afterEach(() => cleanup())

const sleep = (ms) => new Promise(r => setTimeout(r, ms))
const permitCalls = () => apiFetch.mock.calls.filter(([path]) => path === '/api/recording/me').length

async function connectHeadband() {
  render(<Adaptive />)
  const button = await screen.findByRole('button', { name: /connect headband/i })
  await waitFor(() => expect(button).toBeEnabled())
  fireEvent.click(button)
  await screen.findByText(/STREAMING/, {}, { timeout: 10000 })
}

it('keeps a declined camera off: Turn on is disabled, says why, and never starts it', async () => {
  answer = () => buildRecordingPermits({ camera: 'declined' })
  render(<Adaptive />)
  await screen.findByText(DECLINED_CAMERA)
  const turnOn = screen.getByRole('button', { name: /turn on camera/i })
  expect(turnOn).toBeDisabled()
  fireEvent.click(turnOn)
  // The headband has its own answer, so it is not held back by the camera's.
  await waitFor(() => expect(screen.getByRole('button', { name: /connect headband/i })).toBeEnabled())
  expect(deviceStart).not.toHaveBeenCalled()
})

it('says it is checking, not that the check failed, before the first answer lands', async () => {
  answer = () => new Promise(() => {})
  render(<Adaptive />)
  await screen.findByText('Checking whether the camera may record…')
  expect(screen.getByText('Checking whether the headband may record…')).toBeInTheDocument()
  expect(screen.getByRole('button', { name: /turn on camera/i })).toBeDisabled()
})

it('blocks switching on when the check fails, saying it could not check', async () => {
  answer = () => { throw apiError(503) }
  render(<Adaptive />)
  await screen.findByText("Couldn't check whether the camera may record; this retries on its own.")
  expect(screen.getByText("Couldn't check whether the headband may record; this retries on its own."))
    .toBeInTheDocument()
  expect(screen.getByRole('button', { name: /turn on camera/i })).toBeDisabled()
  expect(screen.getByRole('button', { name: /connect headband/i })).toBeDisabled()
})

it('lets the headband connect, and stay connected, for heart rate alone', async () => {
  answer = () => buildRecordingPermits({ eeg: 'declined', camera: 'declined' })
  await connectHeadband()
  expect(deviceStart).toHaveBeenCalledWith('default')
  // Polls keep landing with the same answer; one channel permitted is not a refusal.
  const before = permitCalls()
  await waitFor(() => expect(permitCalls()).toBeGreaterThan(before + 1))
  expect(deviceStop).not.toHaveBeenCalled()
  expect(screen.getByText(/STREAMING/)).toBeInTheDocument()
}, TEST_TIMEOUT)

it('keeps Connect disabled when neither headband channel may record, naming each reason', async () => {
  answer = () => buildRecordingPermits({ eeg: 'declined', headband_optical: 'switched_off' })
  render(<Adaptive />)
  await screen.findByText("Headband recording is off: EEG — a parent hasn't turned it on; "
    + "heart rate — switched off by the school's administrator.")
  expect(screen.getByRole('button', { name: /connect headband/i })).toBeDisabled()
  expect(screen.getByRole('button', { name: /turn on camera/i })).toBeEnabled()
})

it('asks again at the click, and does not start a camera refused since the page loaded', async () => {
  render(<Adaptive />)
  const turnOn = await screen.findByRole('button', { name: /turn on camera/i })
  await waitFor(() => expect(turnOn).toBeEnabled())
  const before = permitCalls()
  answer = () => buildRecordingPermits({ camera: 'declined' })
  fireEvent.click(turnOn)
  await waitFor(() => expect(toast.error).toHaveBeenCalledWith(
    "The camera can't be turned on.", { description: DECLINED_CAMERA }))
  expect(permitCalls()).toBeGreaterThan(before)
  expect(deviceStart).not.toHaveBeenCalled()
})

it('asks again at Connect, and does not pair a headband refused since the page loaded', async () => {
  render(<Adaptive />)
  const connect = await screen.findByRole('button', { name: /connect headband/i })
  await waitFor(() => expect(connect).toBeEnabled())
  answer = () => buildRecordingPermits(HEADBAND_REFUSED)
  fireEvent.click(connect)
  await waitFor(() => expect(toast.error).toHaveBeenCalledWith(
    "The headband can't be connected.", { description: DECLINED_HEADBAND }))
  expect(deviceStart).not.toHaveBeenCalled()
  expect(museRefresh).not.toHaveBeenCalled()
})

it('turns off a running camera when a later answer withdraws it, once', async () => {
  rig.cameraRunning = true
  // A slow stop, so further refusals land while it is still running.
  deviceStop.mockImplementationOnce(() => new Promise(r => setTimeout(() => r({}), 1500)))
  render(<Adaptive />)
  await screen.findByRole('button', { name: /turn off/i })
  await waitFor(() => expect(permitCalls()).toBeGreaterThan(0))
  answer = () => buildRecordingPermits({ camera: 'declined' })
  await waitFor(() => expect(deviceStop).toHaveBeenCalledWith('camera'))
  const before = permitCalls()
  await waitFor(() => expect(permitCalls()).toBeGreaterThan(before + 1))
  await screen.findByRole('button', { name: /turn on camera/i })
  expect(deviceStop).toHaveBeenCalledTimes(1)
  expect(toast.warning).toHaveBeenCalledTimes(1)
  expect(toast.warning).toHaveBeenCalledWith('The camera was turned off.', { description: DECLINED_CAMERA })
})

it('disconnects a connected headband when a later answer withdraws it', async () => {
  await connectHeadband()
  answer = () => buildRecordingPermits(HEADBAND_REFUSED)
  await waitFor(() => expect(deviceStop).toHaveBeenCalledWith('default'))
  await screen.findByRole('button', { name: /connect headband/i })
  expect(toast.warning).toHaveBeenCalledWith('The headband was disconnected.', { description: DECLINED_HEADBAND })
}, TEST_TIMEOUT)

it('stops a headband found running at load when the first answer refuses it', async () => {
  rig.headbandRunning = true
  answer = () => buildRecordingPermits(HEADBAND_REFUSED)
  render(<Adaptive />)
  await waitFor(() => expect(deviceStop).toHaveBeenCalledWith('default'))
  expect(toast.warning).toHaveBeenCalledWith('The headband was disconnected.', { description: DECLINED_HEADBAND })
})

it('turns off a camera found running at load when the first answer refuses it', async () => {
  rig.cameraRunning = true
  answer = () => buildRecordingPermits({ camera: 'switched_off' })
  render(<Adaptive />)
  await waitFor(() => expect(deviceStop).toHaveBeenCalledWith('camera'))
  expect(toast.warning).toHaveBeenCalledWith('The camera was turned off.',
    { description: "Camera recording is off: switched off by the school's administrator." })
})

it('stops a camera whose start lands after it was refused', async () => {
  let started
  deviceStart.mockImplementationOnce(() => new Promise(r => { started = r }))
  render(<Adaptive />)
  const turnOn = await screen.findByRole('button', { name: /turn on camera/i })
  await waitFor(() => expect(turnOn).toBeEnabled())
  fireEvent.click(turnOn)
  await waitFor(() => expect(deviceStart).toHaveBeenCalledWith('camera'))
  answer = () => buildRecordingPermits({ camera: 'declined' })
  const before = permitCalls()
  await waitFor(() => expect(permitCalls()).toBeGreaterThan(before + 1))
  started({})
  await waitFor(() => expect(deviceStop).toHaveBeenCalledWith('camera'))
})

it('stops nothing when a later check fails', async () => {
  rig.cameraRunning = true
  render(<Adaptive />)
  await screen.findByRole('button', { name: /turn off/i })
  await waitFor(() => expect(permitCalls()).toBeGreaterThan(0))
  answer = () => { throw apiError(503) }
  const before = permitCalls()
  await waitFor(() => expect(permitCalls()).toBeGreaterThan(before + 2))
  expect(deviceStop).not.toHaveBeenCalled()
  expect(screen.getByRole('button', { name: /turn off/i })).toBeEnabled()
})

it('lets Turn off work while the check is failing', async () => {
  rig.cameraRunning = true
  answer = () => { throw apiError(503) }
  render(<Adaptive />)
  const off = await screen.findByRole('button', { name: /turn off/i })
  await waitFor(() => expect(permitCalls()).toBeGreaterThan(0))
  expect(off).toBeEnabled()
  fireEvent.click(off)
  await waitFor(() => expect(deviceStop).toHaveBeenCalledWith('camera'))
})

it('ignores an answer that lands after a newer one', async () => {
  let calls = 0
  let release
  let slowStarted
  const slow = new Promise(r => { slowStarted = r })
  answer = () => {
    calls += 1
    if (calls !== 2) return buildRecordingPermits()
    slowStarted()
    return new Promise(r => { release = () => r(buildRecordingPermits({ camera: 'declined' })) })
  }
  render(<Adaptive />)
  const turnOn = await screen.findByRole('button', { name: /turn on camera/i })
  await waitFor(() => expect(turnOn).toBeEnabled())
  // The poll's second request is in flight; the click's is newer and answers at once.
  await slow
  fireEvent.click(turnOn)
  await screen.findByRole('button', { name: /turn off/i })
  release()
  // The poll schedules its next request only once the slow one has settled.
  await waitFor(() => expect(calls).toBeGreaterThanOrEqual(4))
  expect(deviceStop).not.toHaveBeenCalled()
  expect(toast.warning).not.toHaveBeenCalled()
})

it('lets Disconnect work while the check is failing', async () => {
  await connectHeadband()
  answer = () => { throw apiError(503) }
  const before = permitCalls()
  await waitFor(() => expect(permitCalls()).toBeGreaterThan(before))
  expect(deviceStop).not.toHaveBeenCalled()
  const disconnect = screen.getByRole('button', { name: /^disconnect$/i })
  expect(disconnect).toBeEnabled()
  fireEvent.click(disconnect)
  await waitFor(() => expect(deviceStop).toHaveBeenCalledWith('default'))
}, TEST_TIMEOUT)

it('abandons a pairing in progress when the headband is withdrawn', async () => {
  // A scan that finds nothing, so the pairing would otherwise run its full 12 s.
  rig.ingestion = { muse_connected: false, muse_devices: [] }
  render(<Adaptive />)
  const connect = await screen.findByRole('button', { name: /connect headband/i })
  await waitFor(() => expect(connect).toBeEnabled())
  fireEvent.click(connect)
  await waitFor(() => expect(museRefresh).toHaveBeenCalled())
  answer = () => buildRecordingPermits(HEADBAND_REFUSED)
  await waitFor(() => expect(deviceStop).toHaveBeenCalledWith('default'), { timeout: 4000 })
  expect(museConnect).not.toHaveBeenCalled()
  expect(toast.warning).toHaveBeenCalledWith('The headband was disconnected.', { description: DECLINED_HEADBAND })
  // A refusal is not "no headband found".
  expect(toast.error).not.toHaveBeenCalled()
}, TEST_TIMEOUT)

it('stops the reconnect retries when a reconnecting headband is withdrawn', async () => {
  await connectHeadband()
  // The bridge gives up at once, so the page's own loop takes over.
  rig.ingestion = { ...CONNECTED, muse_connected: false, reconnect_exhausted: true, muse_devices: [] }
  await screen.findByText(/reconnecting \(attempt 1 of 3\)/, {}, { timeout: 8000 })
  const disconnects = museDisconnect.mock.calls.length
  answer = () => buildRecordingPermits(HEADBAND_REFUSED)
  await waitFor(() => expect(museDisconnect.mock.calls.length).toBeGreaterThan(disconnects))
  await screen.findByRole('button', { name: /connect headband/i })
  expect(toast.warning).toHaveBeenCalledWith('The headband was disconnected.', { description: DECLINED_HEADBAND })
  // Past the loop's next backoff: no later attempt, and no "could not be reconnected".
  await sleep(5000)
  expect(screen.queryByText(/of 3\)/)).toBeNull()
  expect(toast.error).not.toHaveBeenCalled()
}, TEST_TIMEOUT)

it('does not take over the retries for a headband withdrawn while the bridge was reconnecting it', async () => {
  await connectHeadband()
  rig.ingestion = { ...CONNECTED, muse_connected: false, reconnecting: true, reconnect_attempt: 1 }
  await screen.findByText(/reconnecting \(attempt 1 of 5\)/, {}, { timeout: 8000 })
  // A slow teardown, so the bridge giving up lands while it is still running.
  museDisconnect.mockImplementationOnce(() => new Promise(r => setTimeout(() => r({}), 6000)))
  answer = () => buildRecordingPermits(HEADBAND_REFUSED)
  await waitFor(() => expect(toast.warning).toHaveBeenCalledWith(
    'The headband was disconnected.', { description: DECLINED_HEADBAND }))
  rig.ingestion = { ...CONNECTED, muse_connected: false, reconnect_exhausted: true }
  // Two telemetry ticks see the bridge give up; the page's own "of 3" loop must not start.
  await sleep(4500)
  expect(screen.queryByText(/of 3\)/)).toBeNull()
  await screen.findByRole('button', { name: /connect headband/i }, { timeout: 5000 })
}, TEST_TIMEOUT)
