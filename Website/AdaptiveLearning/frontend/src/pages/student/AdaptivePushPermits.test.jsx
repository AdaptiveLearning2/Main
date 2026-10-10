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
  ...(await importOriginal()), PUSH_POLL_MS: 300, PUSH_STATUS_POLL_MS: 100,
}))

// The sidecar as each test sets it.
const rig = { cameraRunning: false, headbandRunning: false, spare: false, spareRunning: false, ingestion: {},
              sidecar: {}, sidecarDown: false }
vi.mock('../../lib/sidecar', () => ({
  startPush: vi.fn(async () => ({})), stopPush: vi.fn(async () => ({})),
  stopPushOnUnload: vi.fn(),
  // `rig.sidecar` (or what it returns) carries the sidecar's own answer, as its push status reports it.
  pushStatus: vi.fn(async () => {
    if (rig.sidecarDown) throw new Error('sidecar not answering')
    return { enabled: true, running: true, recorded: {},
             ...(typeof rig.sidecar === 'function' ? rig.sidecar() : rig.sidecar) }
  }),
  deviceStart: vi.fn(async () => ({})), deviceStop: vi.fn(async () => ({})),
  deviceStopOnUnload: vi.fn(),
  museRefresh: vi.fn(async () => ({})),
  museConnect: vi.fn(async () => { rig.ingestion = { ...rig.ingestion, muse_connected: true }; return {} }),
  museDisconnect: vi.fn(async () => ({})),
  museState: vi.fn(async () => ({ running: true, ingestion: { ...rig.ingestion } })),
  devices: vi.fn(async () => [
    { device_id: 'default', kind: 'muse', running: rig.headbandRunning },
    ...(rig.spare ? [{ device_id: 'spare', kind: 'muse', running: rig.spareRunning }] : []),
    { device_id: 'camera', kind: 'face', running: rig.cameraRunning },
  ]),
  releasePushIfIdle: vi.fn(async () => ({ stopped: true, devices: [] })),
  sidecarDebug: vi.fn(async () => ({})),
  sidecarKitVersion: vi.fn(async () => null),
}))
vi.mock('../../lib/pageRestore', () => ({ reloadIfRestored: vi.fn() }))
vi.mock('../../context/AuthContext', () => ({
  useAuth: () => ({ user: { id: 'u1', email: 'a@b.c' }, role: 'student', loading: false }),
}))

import { toast } from 'sonner'
import { deviceStart, deviceStop, museConnect, museDisconnect, museRefresh, museState,
         pushStatus, releasePushIfIdle } from '../../lib/sidecar'
import { apiFetch, apiError, mockApi, resetApi } from '../../test/mocks/apiFetch'
import { buildRecordingPermits } from '../../test/fixtures/recordingPermits'
import { announcePermitsChanged } from '../../lib/permitsChanged'
import Adaptive from './Adaptive'

// `eeg_age_ms` is required for "connected": a link counts only with EEG flowing.
const CONNECTED = { muse_connected: true, muse_devices: ['Muse-1'], battery_percent: 80,
                    auto_reconnect: true, reconnecting: false, reconnect_attempt: 0,
                    reconnect_max_attempts: 5, reconnect_exhausted: false, eeg_age_ms: 2 }
const HEADBAND_REFUSED = { eeg: 'declined', headband_optical: 'declined' }
const NOT_PERMITTED = "it isn't permitted (see Sensors on your Profile)"
const DECLINED_CAMERA = `Camera recording is off: ${NOT_PERMITTED}.`
const DECLINED_HEADBAND = `Headband recording is off: ${NOT_PERMITTED}.`
const TEST_TIMEOUT = 60_000

// What the route answers; tests reassign it, and each request reads it when made.
let answer

beforeEach(() => {
  resetApi()
  vi.clearAllMocks()
  rig.cameraRunning = false
  rig.headbandRunning = false
  rig.spare = false
  rig.spareRunning = false
  rig.ingestion = { ...CONNECTED, muse_connected: false }
  rig.sidecar = {}
  rig.sidecarDown = false
  answer = () => buildRecordingPermits()
  mockApi({
    'GET /api/profile/me': () => ({ id: 'u1', role: 'student', grade_level: '4th Grade' }),
    'GET /api/recording/me': () => answer(),
    'GET /api/classes': () => [],
    'GET /api/performance/student/u1': () => [],
    'POST /api/sessions/start': () => ({ id: 'sess-1' }),
    'GET /api/generate-question?bias=0&grade=4th+Grade&session_id=sess-1': () => ({
      id: 'q1', question_text: 'What is 2 + 2?', answer_options: ['3', '4'], correct_answer: '4',
      subject: 'expressions', difficulty: 'easy',
    }),
  })
})

afterEach(() => cleanup())

const sleep = (ms) => new Promise(r => setTimeout(r, ms))
const permitCalls = () => apiFetch.mock.calls.filter(([path]) => path === '/api/recording/me').length
/** Resolves once `n` more answers have been fetched, so the one before the last has been applied. */
const answersLanded = async (n) => {
  const before = permitCalls()
  await waitFor(() => expect(permitCalls()).toBeGreaterThanOrEqual(before + n))
}

async function connectHeadband() {
  render(<Adaptive />)
  const button = await screen.findByRole('button', { name: /connect headband/i })
  await waitFor(() => expect(button).toBeEnabled())
  fireEvent.click(button)
  await screen.findByText(/STREAMING/, {}, { timeout: 10000 })
}

/** A lesson: the session is what starts the push status poll, which carries the sidecar's answer. */
async function startLesson() {
  fireEvent.click(await screen.findByRole('button', { name: /generate question/i }))
  await screen.findByText('What is 2 + 2?')
}

function hideTab() {
  Object.defineProperty(document, 'hidden', { configurable: true, get: () => true })
  document.dispatchEvent(new Event('visibilitychange'))
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

it("follows the backend's sensor verdicts rather than working them out from the states", async () => {
  // Contrived: every state permitted, the camera's verdict not. The page must not re-derive it.
  answer = () => ({ ...buildRecordingPermits(), sensors: { camera: { allowed: false, refused: false },
                                                            headband: { allowed: true, refused: false } } })
  render(<Adaptive />)
  await screen.findByRole('button', { name: /turn on camera/i })
  await answersLanded(2)
  expect(screen.getByRole('button', { name: /turn on camera/i })).toBeDisabled()
  expect(screen.getByRole('button', { name: /connect headband/i })).toBeEnabled()
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

it('words each state for itself: a refusal beside an unchecked channel, and a year never set up', async () => {
  answer = () => buildRecordingPermits({ eeg: 'declined', headband_optical: 'unknown',
                                         camera: 'school_year_unconfigured' })
  render(<Adaptive />)
  await screen.findByText(`Headband recording is off: EEG — ${NOT_PERMITTED}; `
    + "heart rate — couldn't check, this retries on its own.")
  expect(screen.getByText('Camera recording is off: no school year has been set up yet.')).toBeInTheDocument()
  expect(screen.getByRole('button', { name: /connect headband/i })).toBeDisabled()
})

it('lets the headband connect, and stay connected, for heart rate alone', async () => {
  answer = () => buildRecordingPermits({ eeg: 'declined', camera: 'declined' })
  await connectHeadband()
  expect(deviceStart).toHaveBeenCalledWith('default')
  // Answers keep landing; one channel permitted is not a refusal.
  await answersLanded(2)
  expect(deviceStop).not.toHaveBeenCalled()
  expect(screen.getByText(/STREAMING/)).toBeInTheDocument()
}, TEST_TIMEOUT)

it('keeps Connect disabled when neither headband channel may record, naming each reason', async () => {
  answer = () => buildRecordingPermits({ eeg: 'declined', headband_optical: 'switched_off' })
  render(<Adaptive />)
  await screen.findByText(`Headband recording is off: EEG — ${NOT_PERMITTED}; `
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

it('turns off a running camera when a later answer withdraws it, once, and says so after', async () => {
  rig.cameraRunning = true
  // A slow stop, so further refusals land while it is still running.
  let stopped
  deviceStop.mockImplementationOnce(() => new Promise(r => { stopped = r }))
  render(<Adaptive />)
  await screen.findByRole('button', { name: /turn off/i })
  await answersLanded(1)
  answer = () => buildRecordingPermits({ camera: 'declined' })
  await waitFor(() => expect(deviceStop).toHaveBeenCalledWith('camera'))
  await answersLanded(2)
  // Not yet off, so not yet said.
  expect(toast.warning).not.toHaveBeenCalled()
  stopped({})
  await screen.findByRole('button', { name: /turn on camera/i })
  await waitFor(() => expect(toast.warning).toHaveBeenCalledWith(
    'The camera was turned off.', { description: DECLINED_CAMERA }))
  expect(deviceStop.mock.calls).toEqual([['camera']])
  expect(toast.warning).toHaveBeenCalledTimes(1)
})

it('keeps a camera it could not switch off marked on, says so once, and retries', async () => {
  rig.cameraRunning = true
  deviceStop.mockRejectedValueOnce(new Error('sidecar busy')).mockRejectedValueOnce(new Error('sidecar busy'))
  render(<Adaptive />)
  await screen.findByRole('button', { name: /turn off/i })
  answer = () => buildRecordingPermits({ camera: 'declined' })
  // Two failed stops, then the next answer's stop lands.
  await waitFor(() => expect(deviceStop).toHaveBeenCalledTimes(3))
  await screen.findByRole('button', { name: /turn on camera/i })
  expect(toast.error).toHaveBeenCalledTimes(1)
  expect(toast.error).toHaveBeenCalledWith('The camera could not be switched off.',
    { description: `${DECLINED_CAMERA} This retries on its own.` })
  await waitFor(() => expect(toast.warning).toHaveBeenCalledWith(
    'The camera was turned off.', { description: DECLINED_CAMERA }))
  expect(toast.warning).toHaveBeenCalledTimes(1)
})

const stillReleasing = (id) => Object.assign(new Error(`${id} has stopped but is still disconnecting`), { status: 503 })

it('shows a camera whose stream stopped as off while the sidecar still releases it, and stops asking', async () => {
  rig.cameraRunning = true
  deviceStop.mockRejectedValueOnce(stillReleasing('camera'))
  releasePushIfIdle.mockResolvedValueOnce(
    { stopped: true, devices: [{ device_id: 'camera', kind: 'face', running: false }] })
  render(<Adaptive />)
  await screen.findByRole('button', { name: /turn off/i })
  answer = () => buildRecordingPermits({ camera: 'declined' })
  await screen.findByRole('button', { name: /turn on camera/i })
  await waitFor(() => expect(toast.warning).toHaveBeenCalledWith(
    'The camera has stopped but is still being released.', { description: DECLINED_CAMERA }))
  // Shown off, so later refusals send no stop for the sidecar to refuse again.
  await answersLanded(2)
  expect(deviceStop).toHaveBeenCalledTimes(1)
  expect(toast.error).not.toHaveBeenCalled()
  expect(toast.warning).toHaveBeenCalledTimes(1)
})

it('shows a headband whose stream stopped as disconnected while the sidecar still releases it', async () => {
  await connectHeadband()
  deviceStop.mockRejectedValueOnce(stillReleasing('default'))
  answer = () => buildRecordingPermits(HEADBAND_REFUSED)
  await screen.findByRole('button', { name: /connect headband/i })
  await waitFor(() => expect(toast.warning).toHaveBeenCalledWith(
    'The headband has stopped but is still being released.', { description: DECLINED_HEADBAND }))
  await answersLanded(2)
  expect(deviceStop).toHaveBeenCalledTimes(1)
  expect(toast.error).not.toHaveBeenCalled()
}, TEST_TIMEOUT)

it('treats a camera still listed as running after its stop as not stopped', async () => {
  rig.cameraRunning = true
  releasePushIfIdle.mockResolvedValueOnce(
    { stopped: false, devices: [{ device_id: 'camera', kind: 'face', running: true }] })
  render(<Adaptive />)
  await screen.findByRole('button', { name: /turn off/i })
  answer = () => buildRecordingPermits({ camera: 'declined' })
  await waitFor(() => expect(toast.error).toHaveBeenCalledWith('The camera could not be switched off.',
    { description: `${DECLINED_CAMERA} This retries on its own.` }))
  await waitFor(() => expect(deviceStop).toHaveBeenCalledTimes(2))
  await screen.findByRole('button', { name: /turn on camera/i })
})

it('disconnects a connected headband when a later answer withdraws it', async () => {
  await connectHeadband()
  answer = () => buildRecordingPermits(HEADBAND_REFUSED)
  await waitFor(() => expect(deviceStop).toHaveBeenCalledWith('default'))
  await screen.findByRole('button', { name: /connect headband/i })
  await waitFor(() => expect(toast.warning).toHaveBeenCalledWith(
    'The headband was disconnected.', { description: DECLINED_HEADBAND }))
}, TEST_TIMEOUT)

it('keeps a headband it could not disconnect connected, says so, and retries', async () => {
  await connectHeadband()
  deviceStop.mockRejectedValueOnce(new Error('sidecar busy'))
  answer = () => buildRecordingPermits(HEADBAND_REFUSED)
  await waitFor(() => expect(toast.error).toHaveBeenCalledWith('The headband could not be switched off.',
    { description: `${DECLINED_HEADBAND} This retries on its own.` }))
  expect(screen.getByRole('button', { name: /^disconnect$/i })).toBeInTheDocument()
  await waitFor(() => expect(deviceStop).toHaveBeenCalledTimes(2))
  await screen.findByRole('button', { name: /connect headband/i })
  await waitFor(() => expect(toast.warning).toHaveBeenCalledWith(
    'The headband was disconnected.', { description: DECLINED_HEADBAND }))
}, TEST_TIMEOUT)

it('keeps asking while the tab is hidden and a sensor is on, so a withdrawal still lands', async () => {
  rig.cameraRunning = true
  render(<Adaptive />)
  await screen.findByRole('button', { name: /turn off/i })
  await answersLanded(1)
  try {
    hideTab()
    answer = () => buildRecordingPermits({ camera: 'declined' })
    await waitFor(() => expect(deviceStop).toHaveBeenCalledWith('camera'))
  } finally {
    delete document.hidden
  }
})

it('stops asking while the tab is hidden and nothing is on', async () => {
  render(<Adaptive />)
  await screen.findByRole('button', { name: /turn on camera/i })
  // The control: how long three answers take while visible, which bounds the hidden window below.
  const started = Date.now()
  await answersLanded(3)
  const threeAnswers = Date.now() - started
  try {
    hideTab()
    const hiddenAt = permitCalls()
    await sleep(threeAnswers)
    expect(permitCalls()).toBe(hiddenAt)
  } finally {
    delete document.hidden
  }
})

it('turns off a camera found running at load when the first answer refuses it', async () => {
  rig.cameraRunning = true
  answer = () => buildRecordingPermits({ camera: 'switched_off' })
  render(<Adaptive />)
  await waitFor(() => expect(deviceStop).toHaveBeenCalledWith('camera'))
  await waitFor(() => expect(toast.warning).toHaveBeenCalledWith('The camera was turned off.',
    { description: "Camera recording is off: switched off by the school's administrator." }))
})

it('stops a headband found running at load when the first answer refuses it', async () => {
  rig.headbandRunning = true
  answer = () => buildRecordingPermits(HEADBAND_REFUSED)
  render(<Adaptive />)
  await waitFor(() => expect(deviceStop).toHaveBeenCalledWith('default'))
  await waitFor(() => expect(toast.warning).toHaveBeenCalledWith(
    'The headband was disconnected.', { description: DECLINED_HEADBAND }))
})

it('does not stop again a headband the student already disconnected', async () => {
  rig.headbandRunning = true
  await connectHeadband()
  fireEvent.click(screen.getByRole('button', { name: /^disconnect$/i }))
  await screen.findByRole('button', { name: /connect headband/i })
  expect(deviceStop).toHaveBeenCalledTimes(1)
  answer = () => buildRecordingPermits(HEADBAND_REFUSED)
  await answersLanded(2)
  expect(deviceStop).toHaveBeenCalledTimes(1)
  expect(toast.warning).not.toHaveBeenCalled()
}, TEST_TIMEOUT)

it('stops a camera whose start lands after it was refused', async () => {
  let started
  deviceStart.mockImplementationOnce(() => new Promise(r => { started = r }))
  render(<Adaptive />)
  const turnOn = await screen.findByRole('button', { name: /turn on camera/i })
  await waitFor(() => expect(turnOn).toBeEnabled())
  fireEvent.click(turnOn)
  await waitFor(() => expect(deviceStart).toHaveBeenCalledWith('camera'))
  answer = () => buildRecordingPermits({ camera: 'declined' })
  await answersLanded(2)
  started({})
  await waitFor(() => expect(deviceStop).toHaveBeenCalledWith('camera'))
})

it('stops nothing when a later check fails', async () => {
  rig.cameraRunning = true
  render(<Adaptive />)
  await screen.findByRole('button', { name: /turn off/i })
  await answersLanded(1)
  answer = () => { throw apiError(503) }
  await answersLanded(3)
  expect(deviceStop).not.toHaveBeenCalled()
  expect(screen.getByRole('button', { name: /turn off/i })).toBeEnabled()
})

it('lets Turn off work while the check is failing', async () => {
  rig.cameraRunning = true
  answer = () => { throw apiError(503) }
  render(<Adaptive />)
  const off = await screen.findByRole('button', { name: /turn off/i })
  await answersLanded(2)
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
  const afterClick = calls
  release()
  // The old answer settles in microtasks; the next request is a whole interval later.
  await waitFor(() => expect(calls).toBeGreaterThan(afterClick))
  expect(deviceStop).not.toHaveBeenCalled()
  expect(toast.warning).not.toHaveBeenCalled()
})

it('lets Disconnect work while the check is failing', async () => {
  await connectHeadband()
  answer = () => { throw apiError(503) }
  await answersLanded(2)
  expect(deviceStop).not.toHaveBeenCalled()
  const disconnect = screen.getByRole('button', { name: /^disconnect$/i })
  expect(disconnect).toBeEnabled()
  fireEvent.click(disconnect)
  await waitFor(() => expect(deviceStop).toHaveBeenCalledWith('default'))
}, TEST_TIMEOUT)

it('abandons a pairing in progress when the headband is withdrawn, and says so once', async () => {
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
  await waitFor(() => expect(toast.warning).toHaveBeenCalledWith(
    "The headband wasn't connected.", { description: DECLINED_HEADBAND }))
  await answersLanded(2)
  expect(toast.warning).toHaveBeenCalledTimes(1)
  // A refusal is not "no headband found".
  expect(toast.error).not.toHaveBeenCalled()
}, TEST_TIMEOUT)

it('retries the stop of a pairing it abandoned, when that stop failed', async () => {
  rig.ingestion = { muse_connected: false, muse_devices: [] }
  render(<Adaptive />)
  const connect = await screen.findByRole('button', { name: /connect headband/i })
  await waitFor(() => expect(connect).toBeEnabled())
  fireEvent.click(connect)
  await waitFor(() => expect(museRefresh).toHaveBeenCalled())
  deviceStop.mockRejectedValueOnce(new Error('sidecar busy'))
  answer = () => buildRecordingPermits(HEADBAND_REFUSED)
  // The pairing's own stop fails; the station it started is still known to run, so it is stopped again.
  await waitFor(() => expect(deviceStop.mock.calls).toEqual([['default'], ['default']]), { timeout: 5000 })
  await waitFor(() => expect(toast.warning).toHaveBeenCalledWith(
    'The headband was disconnected.', { description: DECLINED_HEADBAND }))
}, TEST_TIMEOUT)

// The sidecar's answer as its push status passes it on: the backend's, observed `age` seconds ago, by a
// sidecar asking every `check` seconds (as short as this file's page poll, so a quiet sidecar shows quickly).
const sidecarSays = (states, age = 0.05, check = 0.3) => ({
  permits: buildRecordingPermits(states), permits_age_seconds: age, permits_check_seconds: check })

/** Render, start a lesson under a fresh sidecar answer, and check the page then asks nothing itself. */
async function inLessonWithTheSidecarAnswering(sidecar = sidecarSays({})) {
  rig.sidecar = sidecar
  render(<Adaptive />)
  // The control: how long three of the page's own answers take, which bounds the windows below.
  const started = Date.now()
  await answersLanded(3)
  const threeAnswers = Date.now() - started
  await startLesson()
  await waitFor(() => expect(pushStatus).toHaveBeenCalled())
  // One window for a request already under way to land, then a window in which none may start.
  await sleep(threeAnswers)
  const quiet = permitCalls()
  await sleep(threeAnswers)
  expect(permitCalls()).toBe(quiet)
  return quiet
}

it("takes the sidecar's answer during a lesson, and turns off what it refuses", async () => {
  // The page's own answer permits the camera; only the sidecar's refuses it.
  rig.cameraRunning = true
  rig.sidecar = sidecarSays({ camera: 'declined' })
  render(<Adaptive />)
  await screen.findByRole('button', { name: /turn off/i })
  await startLesson()
  await waitFor(() => expect(deviceStop).toHaveBeenCalledWith('camera'))
  await screen.findByRole('button', { name: /turn on camera/i })
}, TEST_TIMEOUT)

it("stops asking for itself while the sidecar's answer is recent, and asks again once it is old", async () => {
  const quiet = await inLessonWithTheSidecarAnswering()
  // The sidecar's own checks start failing: it keeps reporting an answer that only grows older.
  rig.sidecar = sidecarSays({}, 100)
  await waitFor(() => expect(permitCalls()).toBeGreaterThan(quiet))
}, TEST_TIMEOUT)

it("trusts the sidecar's answer for as long as the sidecar's own check interval says", async () => {
  // Asking every 10 s, an answer 1 s old is current, though older than 1.5 of the page's own polls.
  await inLessonWithTheSidecarAnswering(sidecarSays({}, 1, 10))
}, TEST_TIMEOUT)

it('keeps taking answers after the clock is set back', async () => {
  rig.cameraRunning = true
  render(<Adaptive />)
  await screen.findByRole('button', { name: /turn off/i })
  await answersLanded(1)
  const realNow = Date.now.bind(Date)
  const behind = vi.spyOn(Date, 'now').mockImplementation(() => realNow() - 3_600_000)
  try {
    answer = () => buildRecordingPermits({ camera: 'declined' })
    await waitFor(() => expect(deviceStop).toHaveBeenCalledWith('camera'))
  } finally {
    behind.mockRestore()
  }
})

it('acts on a refusal it held back for a Turn off that then failed, though the answer never changes', async () => {
  rig.cameraRunning = true
  render(<Adaptive />)
  const off = await screen.findByRole('button', { name: /turn off/i })
  await answersLanded(1)
  let failTurnOff
  deviceStop.mockImplementationOnce(() => new Promise((_, reject) => { failTurnOff = reject }))
  // After the failed Turn off, the device list still shows the camera.
  releasePushIfIdle.mockResolvedValueOnce(
    { stopped: false, devices: [{ device_id: 'camera', kind: 'face', running: true }] })
  fireEvent.click(off)
  await waitFor(() => expect(deviceStop).toHaveBeenCalledTimes(1))
  // The refusal lands while that Turn off is still under way, so it is left to it.
  answer = () => buildRecordingPermits({ camera: 'declined' })
  await answersLanded(2)
  expect(deviceStop).toHaveBeenCalledTimes(1)
  failTurnOff(new Error('sidecar busy'))
  await waitFor(() => expect(deviceStop).toHaveBeenCalledTimes(2))
  await screen.findByRole('button', { name: /turn on camera/i })
})

it('asks for itself again when the sidecar stops answering at all', async () => {
  const quiet = await inLessonWithTheSidecarAnswering()
  rig.sidecarDown = true
  await waitFor(() => expect(permitCalls()).toBeGreaterThan(quiet))
}, TEST_TIMEOUT)

it("does not let the sidecar's older answer undo a refusal the page has just had", async () => {
  rig.sidecar = {}
  render(<Adaptive />)
  await startLesson()
  const turnOn = await screen.findByRole('button', { name: /turn on camera/i })
  await waitFor(() => expect(turnOn).toBeEnabled())
  // From here the sidecar keeps reporting a permit it observed just before the click.
  const observed = Date.now()
  rig.sidecar = () => sidecarSays({}, (Date.now() - observed) / 1000 + 0.001)
  const reEnabled = []
  const watch = new MutationObserver(() => { if (!turnOn.disabled) reEnabled.push(Date.now()) })
  watch.observe(turnOn, { attributes: true, attributeFilter: ['disabled'] })
  try {
    answer = () => buildRecordingPermits({ camera: 'declined' })
    fireEvent.click(turnOn)
    await waitFor(() => expect(toast.error).toHaveBeenCalledWith(
      "The camera can't be turned on.", { description: DECLINED_CAMERA }))
    // Several of the sidecar's statuses land, each older than the click's answer.
    const statuses = pushStatus.mock.calls.length
    await waitFor(() => expect(pushStatus.mock.calls.length).toBeGreaterThanOrEqual(statuses + 3))
    expect(reEnabled).toEqual([])
    expect(turnOn).toBeDisabled()
  } finally {
    watch.disconnect()
  }
}, TEST_TIMEOUT)

it('stops every running headband station on a refusal, not only the connected one', async () => {
  rig.spare = true
  rig.spareRunning = true
  render(<Adaptive />)
  fireEvent.change(await screen.findByRole('combobox', { name: 'Headband' }), { target: { value: 'default' } })
  const connect = screen.getByRole('button', { name: /connect headband/i })
  await waitFor(() => expect(connect).toBeEnabled())
  fireEvent.click(connect)
  await screen.findByText(/STREAMING/, {}, { timeout: 10000 })
  answer = () => buildRecordingPermits(HEADBAND_REFUSED)
  await waitFor(() => expect(deviceStop.mock.calls.map(([id]) => id).sort()).toEqual(['default', 'spare']))
  await waitFor(() => expect(toast.warning).toHaveBeenCalledWith(
    'The headband was disconnected.', { description: DECLINED_HEADBAND }))
  // Together, as one: a refusal is announced once, not once per station.
  await answersLanded(2)
  expect(toast.warning).toHaveBeenCalledTimes(1)
}, TEST_TIMEOUT)

it('announces a station that failed to stop even when another is still being released', async () => {
  rig.spare = true
  rig.spareRunning = true
  // The connected station's 503 lands first; the spare's real failure after it, then its retry succeeds.
  const firstRound = async (id) => {
    if (id === 'default') throw stillReleasing('default')
    await new Promise(r => setTimeout(r, 50))
    throw new Error('sidecar busy')
  }
  deviceStop.mockImplementationOnce(firstRound).mockImplementationOnce(firstRound)
  render(<Adaptive />)
  fireEvent.change(await screen.findByRole('combobox', { name: 'Headband' }), { target: { value: 'default' } })
  const connect = screen.getByRole('button', { name: /connect headband/i })
  await waitFor(() => expect(connect).toBeEnabled())
  fireEvent.click(connect)
  await screen.findByText(/STREAMING/, {}, { timeout: 10000 })
  answer = () => buildRecordingPermits(HEADBAND_REFUSED)
  await waitFor(() => expect(toast.error).toHaveBeenCalledWith('The headband could not be switched off.',
    { description: `${DECLINED_HEADBAND} This retries on its own.` }))
  await waitFor(() => expect(deviceStop.mock.calls.filter(([id]) => id === 'spare')).toHaveLength(2))
  expect(toast.warning).not.toHaveBeenCalledWith('The headband has stopped but is still being released.',
    expect.anything())
}, TEST_TIMEOUT)

it('stops an abandoned pairing once, and says so once, however slow the stop', async () => {
  rig.ingestion = { muse_connected: false, muse_devices: [] }
  let stopped
  deviceStop.mockImplementationOnce(() => new Promise(r => { stopped = r }))
  render(<Adaptive />)
  const connect = await screen.findByRole('button', { name: /connect headband/i })
  await waitFor(() => expect(connect).toBeEnabled())
  fireEvent.click(connect)
  await waitFor(() => expect(museRefresh).toHaveBeenCalled())
  answer = () => buildRecordingPermits(HEADBAND_REFUSED)
  await waitFor(() => expect(deviceStop).toHaveBeenCalledWith('default'), { timeout: 4000 })
  // Answers land while the pairing's own stop is still under way.
  await answersLanded(2)
  stopped({})
  await answersLanded(2)
  expect(deviceStop).toHaveBeenCalledTimes(1)
  expect(toast.warning.mock.calls.map(([title]) => title)).toEqual(["The headband wasn't connected."])
}, TEST_TIMEOUT)

it('says again that a camera could not be switched off, once it was seen off in between', async () => {
  rig.cameraRunning = true
  deviceStop.mockRejectedValue(new Error('sidecar busy'))
  try {
    render(<Adaptive />)
    await screen.findByRole('button', { name: /turn off/i })
    answer = () => buildRecordingPermits({ camera: 'declined' })
    await waitFor(() => expect(toast.error).toHaveBeenCalledTimes(1))
    // Off by hand: the stop errors, but the device list no longer shows the camera.
    fireEvent.click(screen.getByRole('button', { name: /turn off/i }))
    const turnOn = await screen.findByRole('button', { name: /turn on camera/i })
    answer = () => buildRecordingPermits()
    await waitFor(() => expect(turnOn).toBeEnabled())
    fireEvent.click(turnOn)
    await screen.findByRole('button', { name: /turn off/i })
    answer = () => buildRecordingPermits({ camera: 'declined' })
    await waitFor(() => expect(toast.error).toHaveBeenCalledTimes(2))
  } finally {
    deviceStop.mockImplementation(async () => ({}))
  }
})

it('marks a station stopped when the device list says so, even if its stop call errored', async () => {
  await connectHeadband()
  deviceStop.mockRejectedValueOnce(new Error('timed out'))
  releasePushIfIdle.mockResolvedValueOnce(
    { stopped: true, devices: [{ device_id: 'default', kind: 'muse', running: false }] })
  fireEvent.click(screen.getByRole('button', { name: /^disconnect$/i }))
  await screen.findByRole('button', { name: /connect headband/i })
  answer = () => buildRecordingPermits(HEADBAND_REFUSED)
  await answersLanded(2)
  expect(deviceStop).toHaveBeenCalledTimes(1)
  expect(toast.warning).not.toHaveBeenCalled()
}, TEST_TIMEOUT)

it('ends the reconnect loop for a withdrawn headband, so it never runs out and says it could not reconnect', async () => {
  // Every timer recorded, so the test can wait for the loop's backoff to end rather than for a guessed time.
  const realSetTimeout = globalThis.setTimeout
  const timers = []
  const spy = vi.spyOn(globalThis, 'setTimeout').mockImplementation((fn, ms, ...rest) => {
    const timer = { ms, fired: false }
    timers.push(timer)
    return realSetTimeout(() => { timer.fired = true; fn(...rest) }, ms)
  })
  try {
    await connectHeadband()
    rig.ingestion = { ...CONNECTED, muse_connected: false, reconnect_exhausted: true, muse_devices: [] }
    await screen.findByText(/reconnecting \(attempt 1 of 3\)/, {}, { timeout: 8000 })
    const backoff = timers.findLast(t => t.ms === 2000)
    const mark = timers.length
    answer = () => buildRecordingPermits(HEADBAND_REFUSED)
    await screen.findByRole('button', { name: /connect headband/i })
    // A loop still running schedules attempt 2's 4 s backoff straight after attempt 1's ends.
    await waitFor(() => expect(backoff.fired).toBe(true))
    await new Promise(r => realSetTimeout(r, 0))
    expect(timers.slice(mark).some(t => t.ms === 4000)).toBe(false)
    expect(toast.error).not.toHaveBeenCalled()
  } finally {
    spy.mockRestore()
  }
}, TEST_TIMEOUT)

it('stops the reconnect retries when a reconnecting headband is withdrawn', async () => {
  await connectHeadband()
  // The bridge gives up at once, so the page's own loop takes over.
  rig.ingestion = { ...CONNECTED, muse_connected: false, reconnect_exhausted: true, muse_devices: [] }
  await screen.findByText(/reconnecting \(attempt 1 of 3\)/, {}, { timeout: 8000 })
  const disconnects = museDisconnect.mock.calls.length
  answer = () => buildRecordingPermits(HEADBAND_REFUSED)
  // Stop trying's teardown; that it ends the loop is pinned by the test above.
  await waitFor(() => expect(museDisconnect.mock.calls.length).toBeGreaterThan(disconnects))
  await screen.findByRole('button', { name: /connect headband/i })
  await waitFor(() => expect(toast.warning).toHaveBeenCalledWith(
    'The headband was disconnected.', { description: DECLINED_HEADBAND }))
}, TEST_TIMEOUT)

it('does not take over the retries for a headband withdrawn while the bridge was reconnecting it', async () => {
  await connectHeadband()
  rig.ingestion = { ...CONNECTED, muse_connected: false, reconnecting: true, reconnect_attempt: 1 }
  await screen.findByText(/reconnecting \(attempt 1 of 5\)/, {}, { timeout: 8000 })
  // A slow teardown, so the bridge giving up lands while it is still running.
  museDisconnect.mockImplementationOnce(() => new Promise(r => setTimeout(() => r({}), 6000)))
  const disconnects = museDisconnect.mock.calls.length
  answer = () => buildRecordingPermits(HEADBAND_REFUSED)
  await waitFor(() => expect(museDisconnect.mock.calls.length).toBeGreaterThan(disconnects))
  rig.ingestion = { ...CONNECTED, muse_connected: false, reconnect_exhausted: true }
  // Each telemetry tick is where a takeover would start; by the second read the first tick has acted.
  const ticks = museState.mock.calls.length
  await waitFor(() => expect(museState.mock.calls.length).toBeGreaterThanOrEqual(ticks + 2))
  expect(screen.queryByText(/of 3\)/)).toBeNull()
  // Answers kept landing during the slow teardown; it is still the only one.
  expect(museDisconnect).toHaveBeenCalledTimes(disconnects + 1)
  await screen.findByRole('button', { name: /connect headband/i }, { timeout: 8000 })
}, TEST_TIMEOUT)

// A withdrawal reaches the backend's gate at the next batch, long before a check is due: these ask at once.
const pollsLanded = async (n) => {
  const before = pushStatus.mock.calls.length
  await waitFor(() => expect(pushStatus.mock.calls.length).toBeGreaterThanOrEqual(before + n))
}

it('asks at once when a channel turns to declined, and turns off the camera the answer refuses', async () => {
  rig.cameraRunning = true
  await inLessonWithTheSidecarAnswering()
  answer = () => buildRecordingPermits({ camera: 'declined' })
  // The sidecar's answer, still fresh, permits it: only the page asking again can stop the camera.
  rig.sidecar = { ...sidecarSays({}), last_result: { face: 'declined' } }
  await waitFor(() => expect(deviceStop).toHaveBeenCalledWith('camera'))
}, TEST_TIMEOUT)

it('asks once per turn to declined, and never for a recorded result', async () => {
  const quiet = await inLessonWithTheSidecarAnswering()
  rig.sidecar = { ...sidecarSays({}), last_result: { cognitive: 'recorded', face: 'recorded' } }
  await pollsLanded(3)
  expect(permitCalls()).toBe(quiet)
  // An unworn headband: declined batch after batch, and still permitted.
  rig.sidecar = { ...sidecarSays({}), last_result: { cognitive: 'declined', face: 'recorded' } }
  await waitFor(() => expect(permitCalls()).toBe(quiet + 1))
  await pollsLanded(3)
  expect(permitCalls()).toBe(quiet + 1)
  // A gap (the result goes stale), then declined again: the same run, not a new one.
  rig.sidecar = { ...sidecarSays({}), last_result: { cognitive: null, face: 'recorded' } }
  await pollsLanded(3)
  rig.sidecar = { ...sidecarSays({}), last_result: { cognitive: 'declined', face: 'recorded' } }
  await pollsLanded(3)
  expect(permitCalls()).toBe(quiet + 1)
}, TEST_TIMEOUT)

it('leaves asking to a sidecar that re-asks on a refusal itself', async () => {
  const quiet = await inLessonWithTheSidecarAnswering()
  rig.sidecar = { ...sidecarSays({}), checks_on_refusal: true, last_result: { face: 'declined' } }
  await pollsLanded(3)
  expect(permitCalls()).toBe(quiet)
}, TEST_TIMEOUT)

it('asks at once when consent changes on another page of this browser', async () => {
  rig.cameraRunning = true
  await inLessonWithTheSidecarAnswering()
  answer = () => buildRecordingPermits({ camera: 'declined' })
  announcePermitsChanged()
  await waitFor(() => expect(deviceStop).toHaveBeenCalledWith('camera'))
}, TEST_TIMEOUT)
