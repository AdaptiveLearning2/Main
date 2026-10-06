/** One sidecar delivers one lesson at a time; a tab whose lesson it isn't says so, and stops or takes nothing. */
import { it, expect, beforeEach, afterEach, vi } from 'vitest'
import { act, render, screen, cleanup, fireEvent, waitFor, within } from '@testing-library/react'

vi.mock('../../lib/api', async () => await import('../../test/mocks/apiFetch'))
vi.mock('../../lib/supabase', async () => await import('../../test/mocks/supabase'))
vi.mock('sonner', () => ({ toast: { error: vi.fn(), success: vi.fn(), warning: vi.fn() } }))
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
// Short, so the status poll lands several times inside a test.
vi.mock('./pollIntervals', async (importOriginal) => ({
  ...(await importOriginal()), PUSH_STATUS_POLL_MS: 100,
}))
// The sidecar as each test sets it: `owner` is the lesson it delivers for, and a start takes it, as the real one does.
const rig = { owner: null, down: false, hang: false, polls: 0, cameraRunning: true,
              lagRelease: false, hold: false, held: [], bridge: { running: false, ingestion: {} } }
// A stop naming another session changes nothing, as the sidecar's own check has it.
const release = (sessionId) => { if (!sessionId || sessionId === rig.owner) rig.owner = null }
vi.mock('../../lib/sidecar', () => ({
  startPush: vi.fn(async (sessionId) => { rig.owner = sessionId; return {} }),
  // `lagRelease`: still winding the lesson down, so its status goes on naming it for a while.
  stopPush: vi.fn(async (sessionId) => { if (!rig.lagRelease) release(sessionId); return {} }),
  stopPushOnUnload: vi.fn((sessionId) => release(sessionId)),
  pushStatus: vi.fn(async () => {
    if (rig.hang) return new Promise(() => {})
    if (rig.down) throw new Error('sidecar not answering')
    // Rising, as a lesson that is recording: the recording chip is built from the rise between two polls.
    const n = ++rig.polls
    const reply = { enabled: true, running: rig.owner != null, session_id: rig.owner,
                    recorded: { cognitive: 30 + n, heart: 9 + n, face: 40 + n },
                    rejected: { cognitive: 0, heart: 0, face: 2 },
                    last_result: { cognitive: 'declined', heart: 'recorded', face: 'recorded' },
                    declined_reason: { cognitive: 'eeg not consented', heart: null, face: null } }
    // `hold`: answered as the sidecar was when asked, but only when the test lets it arrive.
    return rig.hold ? new Promise(r => rig.held.push(() => r(reply))) : reply
  }),
  deviceStart: vi.fn(async () => ({})), deviceStop: vi.fn(async () => ({})),
  deviceStopOnUnload: vi.fn(),
  museRefresh: vi.fn(async () => ({})),
  museConnect: vi.fn(async () => ({})),
  museDisconnect: vi.fn(async () => ({})),
  museState: vi.fn(async () => ({ running: rig.bridge.running, ingestion: { ...rig.bridge.ingestion } })),
  devices: vi.fn(async () => [
    { device_id: 'default', kind: 'muse', running: rig.bridge.running },
    { device_id: 'camera', kind: 'face', running: rig.cameraRunning },
  ]),
  releasePushIfIdle: vi.fn(async () => ({ stopped: true, devices: [] })),
  sidecarDebug: vi.fn(async () => ({})),
  sidecarState: vi.fn(async () => null),
  debugFaceFrame: vi.fn(async () => null),
}))
vi.mock('../../context/AuthContext', () => ({
  useAuth: () => ({ user: { id: 'u1', email: 'a@b.c' }, role: 'student', loading: false }),
}))

import { markEegStarted, recordAnswer } from '../../lib/session'
import { deviceStop, deviceStopOnUnload, pushStatus, releasePushIfIdle, startPush, stopPush,
         stopPushOnUnload } from '../../lib/sidecar'
import { fireAuthEvent } from '../../test/mocks/supabase'
import { runSignOutTasks } from '../../lib/signOutTasks'
import { mockApi, resetApi } from '../../test/mocks/apiFetch'
import { buildRecordingPermits } from '../../test/fixtures/recordingPermits'
import Adaptive from './Adaptive'

// `eeg_age_ms` is required for "connected": a link counts only with EEG flowing.
const CONNECTED = { muse_connected: true, muse_devices: ['Muse-1'], battery_percent: 80,
                    auto_reconnect: true, reconnecting: false, reconnect_attempt: 0,
                    reconnect_max_attempts: 5, reconnect_exhausted: false, eeg_age_ms: 2,
                    active_muse_name: 'Muse-1' }
const ELSEWHERE = 'This lesson is also open in another tab or window. Readings go there.'

beforeEach(() => {
  resetApi()
  vi.clearAllMocks()
  Object.assign(rig, { owner: null, down: false, hang: false, polls: 0, cameraRunning: true,
                       lagRelease: false, hold: false, held: [], bridge: { running: false, ingestion: {} } })
  mockApi({
    'GET /api/profile/me': () => ({ id: 'u1', role: 'student', grade_level: '1st Grade' }),
    'GET /api/recording/me': () => buildRecordingPermits(),
    'GET /api/classes': () => [],
    'POST /api/sessions/start': () => ({ id: 'sess-push' }),
    'GET /api/performance/student/u1': () => [],
    'GET /api/generate-question?bias=0&grade=1st+Grade&session_id=sess-push': () => ({
      id: 'q1', question_text: 'What is 2 + 2?', question_topic: 'ordering',
      answer_options: ['3', '4', '5'], correct_answer: '4', difficulty: 'easy',
    }),
  })
})

afterEach(() => cleanup())

const sleep = (ms) => new Promise(r => setTimeout(r, ms))
const panelOf = (title) => screen.getByText(title).closest('div')
const pollsLanded = async (n) => {
  const before = pushStatus.mock.calls.length
  await waitFor(() => expect(pushStatus.mock.calls.length).toBeGreaterThanOrEqual(before + n))
}

/** Start this tab's lesson, which hands it to the sidecar, then let another tab's lesson take delivery over. */
async function inLessonTakenOver() {
  render(<Adaptive />)
  fireEvent.click(await screen.findByRole('button', { name: /generate question/i }))
  await screen.findByText('What is 2 + 2?')
  await waitFor(() => expect(startPush).toHaveBeenCalledWith('sess-push'))
  rig.owner = 'sess-other'
  await screen.findByText(ELSEWHERE)
}

it("claims nothing from another tab's lesson, and says where the readings go", async () => {
  await inLessonTakenOver()

  const camera = panelOf('Camera')
  expect(within(camera).getByText('on, in another tab')).toBeInTheDocument()
  expect(within(camera).getByText(/Its readings go to the lesson in another tab or window/)).toBeInTheDocument()
  expect(screen.queryByText('● RECORDING')).toBeNull()
  // That lesson's declined EEG and lost readings are not this one's.
  expect(screen.queryByText(/Not being saved/)).toBeNull()
  expect(screen.queryByText(/not saved/)).toBeNull()
  expect(within(panelOf('Muse Headband')).queryByText('not recording')).toBeNull()
  // Nor its delivery: the recording chip, shown once an answer is saved, is built from the counts between polls.
  recordAnswer.mockResolvedValueOnce({ ok: true, topic: 'ordering' })
  fireEvent.click(screen.getByRole('button', { name: /^B\s*4$/ }))
  fireEvent.click(screen.getByRole('button', { name: /submit answer/i }))
  await screen.findByText(/1 answered/)
  await pollsLanded(3)
  expect(screen.queryByText(/Recording:/)).toBeNull()
}, 30_000)

it('takes delivery back with Use this tab, and reads as its own lesson at once', async () => {
  await inLessonTakenOver()
  const starts = startPush.mock.calls.length
  // No status answers after the click: only the start's own success can say whose lesson it is now.
  rig.hang = true

  fireEvent.click(screen.getByRole('button', { name: 'Use this tab' }))

  await waitFor(() => expect(screen.queryByText(ELSEWHERE)).toBeNull())
  expect(startPush.mock.calls.slice(starts)).toEqual([['sess-push']])
  expect(await within(panelOf('Camera')).findByText('● RECORDING')).toBeInTheDocument()
}, 30_000)

it("stops neither another tab's lesson nor its camera on a tab close or a route change", async () => {
  await inLessonTakenOver()

  window.dispatchEvent(new Event('pagehide'))
  cleanup()
  await sleep(50)

  expect(stopPushOnUnload).not.toHaveBeenCalled()
  expect(deviceStopOnUnload).not.toHaveBeenCalled()
  expect(stopPush).not.toHaveBeenCalled()
  expect(deviceStop).not.toHaveBeenCalled()
}, 30_000)

it("turns the shared camera off from the other tab without releasing that lesson's delivery", async () => {
  await inLessonTakenOver()

  fireEvent.click(within(panelOf('Camera').parentElement).getByRole('button', { name: 'Turn off' }))

  await waitFor(() => expect(deviceStop).toHaveBeenCalledWith('camera'))
  await sleep(50)
  expect(releasePushIfIdle).not.toHaveBeenCalled()
  expect(stopPush).not.toHaveBeenCalled()
}, 30_000)

it('stops its own lesson by name, and its camera, on a tab close and a route change', async () => {
  render(<Adaptive />)
  fireEvent.click(await screen.findByRole('button', { name: /generate question/i }))
  await screen.findByText('What is 2 + 2?')
  await pollsLanded(2)

  window.dispatchEvent(new Event('pagehide'))
  cleanup()

  expect(stopPushOnUnload).toHaveBeenCalledWith('sess-push')
  expect(deviceStopOnUnload).toHaveBeenCalledWith('camera')
  await waitFor(() => expect(stopPush).toHaveBeenCalledWith('sess-push'))
  expect(deviceStop).toHaveBeenCalledWith('camera')
}, 30_000)

it("does not take another tab's lesson back on a token refresh, and re-hands its own", async () => {
  await inLessonTakenOver()
  const starts = startPush.mock.calls.length

  fireEvent.click(screen.getByRole('button', { name: 'Use this tab' }))
  await waitFor(() => expect(screen.queryByText(ELSEWHERE)).toBeNull())
  fireAuthEvent('TOKEN_REFRESHED', { access_token: 'fresh' })
  await waitFor(() => expect(startPush).toHaveBeenLastCalledWith('sess-push', 'fresh'))

  rig.owner = 'sess-other'
  await screen.findByText(ELSEWHERE)
  const before = startPush.mock.calls.length
  fireAuthEvent('TOKEN_REFRESHED', { access_token: 'fresher' })
  await pollsLanded(3)
  expect(startPush.mock.calls.length).toBe(before)
  expect(starts).toBeGreaterThan(0)
}, 30_000)

it("does not take another tab's lesson back when a status read fails, and recovers its own", async () => {
  await inLessonTakenOver()
  const starts = startPush.mock.calls.length
  rig.down = true
  await sleep(500)
  expect(startPush.mock.calls.length).toBe(starts)

  // Its own lesson: a failed read re-offers it, as before.
  rig.down = false
  fireEvent.click(screen.getByRole('button', { name: 'Use this tab' }))
  await waitFor(() => expect(screen.queryByText(ELSEWHERE)).toBeNull())
  const mine = startPush.mock.calls.length
  rig.down = true
  await waitFor(() => expect(startPush.mock.calls.length).toBeGreaterThan(mine))
}, 30_000)

it('takes an idle sidecar, as when the tab that had it closed', async () => {
  await inLessonTakenOver()
  const starts = startPush.mock.calls.length
  rig.owner = null

  await waitFor(() => expect(startPush.mock.calls.length).toBeGreaterThan(starts))
  expect(startPush).toHaveBeenLastCalledWith('sess-push')
  await waitFor(() => expect(screen.queryByText(ELSEWHERE)).toBeNull())
}, 30_000)

it("says a connected headband's readings go to the other tab, and reports no start for this lesson", async () => {
  rig.bridge = { running: true, ingestion: { ...CONNECTED } }
  await inLessonTakenOver()
  const connect = await screen.findByRole('button', { name: /connect headband/i })
  await waitFor(() => expect(connect).not.toBeDisabled())
  fireEvent.click(connect)
  await screen.findByText(/STREAMING/, {}, { timeout: 10000 })

  const headband = panelOf('Muse Headband')
  expect(await within(headband).findByText('in another tab')).toBeInTheDocument()
  expect(screen.getByText('Connected. Your readings go to the lesson in another tab or window.')).toBeInTheDocument()
  expect(screen.queryByText(/teacher can see your focus/)).toBeNull()
  await pollsLanded(3)
  expect(markEegStarted).not.toHaveBeenCalled()
}, 60_000)

// A tab with no lesson of its own: it found the camera running at load, and only the sidecar says whose it is.
it("leaves another tab's lesson's camera on when a tab with no lesson closes", async () => {
  rig.owner = 'sess-other'
  render(<Adaptive />)
  await screen.findByText('Camera')
  expect(await within(panelOf('Camera')).findByText('on, in another tab')).toBeInTheDocument()
  // The sentence agrees with the chip, rather than saying nothing records until a lesson starts.
  expect(within(panelOf('Camera')).getByText(/Its readings go to the lesson in another tab or window/))
    .toBeInTheDocument()

  window.dispatchEvent(new Event('pagehide'))
  cleanup()
  await sleep(50)

  expect(deviceStopOnUnload).not.toHaveBeenCalled()
  expect(deviceStop).not.toHaveBeenCalled()
}, 30_000)

it('still switches the camera off when a tab with no lesson closes and no lesson is using it', async () => {
  render(<Adaptive />)
  await screen.findByRole('button', { name: 'Turn off' })
  await pollsLanded(2)
  // The status is read only for whose lesson it is: before a lesson the cards say what they always said.
  expect(within(panelOf('Muse Headband')).queryByText('not recording')).toBeNull()

  window.dispatchEvent(new Event('pagehide'))

  expect(deviceStopOnUnload).toHaveBeenCalledWith('camera')
}, 30_000)

it('reads as its own lesson as soon as it starts one, not at the next poll', async () => {
  rig.owner = 'sess-other'
  render(<Adaptive />)
  await screen.findByText('Camera')
  await within(panelOf('Camera')).findByText('on, in another tab')
  // No status answers from here: only the hand-over's own success can say the lesson is now this tab's.
  rig.hang = true

  fireEvent.click(await screen.findByRole('button', { name: /generate question/i }))
  await screen.findByText('What is 2 + 2?')
  await waitFor(() => expect(startPush).toHaveBeenCalledWith('sess-push'))

  await waitFor(() => expect(within(panelOf('Camera')).queryByText('on, in another tab')).toBeNull())
  expect(screen.queryByText(ELSEWHERE)).toBeNull()
}, 30_000)

it("does not read its own ended lesson as another tab's", async () => {
  render(<Adaptive />)
  fireEvent.click(await screen.findByRole('button', { name: /generate question/i }))
  await screen.findByText('What is 2 + 2?')
  await pollsLanded(2)
  rig.hang = true

  // Signing out ends the lesson with the page still open, as Finish session does.
  await act(async () => { await runSignOutTasks() })

  await waitFor(() => expect(stopPush).toHaveBeenCalledWith('sess-push'))
  await sleep(50)
  expect(screen.queryByText('on, in another tab')).toBeNull()
}, 30_000)

it('shows the camera off after taking over from a tab that switched it off when it closed', async () => {
  await inLessonTakenOver()
  expect(screen.getByRole('button', { name: 'Turn off' })).toBeInTheDocument()

  // The other tab closes: its own stop releases the sidecar and switches the shared camera off.
  rig.cameraRunning = false
  rig.owner = null

  expect(await screen.findByRole('button', { name: /turn on camera/i })).toBeInTheDocument()
  expect(startPush).toHaveBeenLastCalledWith('sess-push')
}, 30_000)

it('says nothing new before a lesson when the status cannot be read', async () => {
  rig.down = true
  render(<Adaptive />)
  await screen.findByText('Muse Headband')
  await pollsLanded(3)

  expect(screen.queryByText(/is not running, so nothing is being recorded/)).toBeNull()
  expect(startPush).not.toHaveBeenCalled()
}, 30_000)

it('switches the camera off on closing after its own lesson ended, while the sidecar still names that lesson', async () => {
  // Another tab's lesson holds the sidecar first, so this lesson is this tab's by its own hand-over, not a recovery.
  rig.owner = 'sess-other'
  render(<Adaptive />)
  await screen.findByText('Camera')
  await within(panelOf('Camera')).findByText('on, in another tab')
  fireEvent.click(await screen.findByRole('button', { name: /generate question/i }))
  await screen.findByText('What is 2 + 2?')
  await pollsLanded(2)
  rig.lagRelease = true

  await act(async () => { await runSignOutTasks() })
  await waitFor(() => expect(stopPush).toHaveBeenCalledWith('sess-push'))
  // Replies that still name the ended lesson: it is winding down, not another tab's.
  await pollsLanded(2)
  expect(screen.queryByText('on, in another tab')).toBeNull()

  window.dispatchEvent(new Event('pagehide'))
  expect(deviceStopOnUnload).toHaveBeenCalledWith('camera')
}, 30_000)

it('is not undone by a status reply sent before Use this tab', async () => {
  await inLessonTakenOver()
  rig.hold = true
  // A reply naming the other lesson is on its way.
  await waitFor(() => expect(rig.held.length).toBeGreaterThan(0))

  fireEvent.click(screen.getByRole('button', { name: 'Use this tab' }))
  await waitFor(() => expect(screen.queryByText(ELSEWHERE)).toBeNull())
  rig.held.splice(0).forEach(arrive => arrive())
  await sleep(50)

  expect(screen.queryByText(ELSEWHERE)).toBeNull()
}, 30_000)

it('is not undone by a status reply sent before its own lesson started', async () => {
  rig.owner = 'sess-other'
  render(<Adaptive />)
  await screen.findByText('Camera')
  await within(panelOf('Camera')).findByText('on, in another tab')
  rig.hold = true
  await waitFor(() => expect(rig.held.length).toBeGreaterThan(0))

  fireEvent.click(await screen.findByRole('button', { name: /generate question/i }))
  await screen.findByText('What is 2 + 2?')
  await waitFor(() => expect(within(panelOf('Camera')).queryByText('on, in another tab')).toBeNull())
  rig.held.splice(0).forEach(arrive => arrive())
  await sleep(50)

  expect(within(panelOf('Camera')).queryByText('on, in another tab')).toBeNull()
  expect(screen.queryByText(ELSEWHERE)).toBeNull()
}, 30_000)

it('knows a lesson the status poll handed over, when the first hand-over failed, as its own once ended', async () => {
  // The sidecar was not up for the first hand-over; the poll's recovery is what gives it the lesson.
  startPush.mockRejectedValueOnce(new Error('sidecar not up yet'))
  render(<Adaptive />)
  fireEvent.click(await screen.findByRole('button', { name: /generate question/i }))
  await screen.findByText('What is 2 + 2?')
  await waitFor(() => expect(rig.owner).toBe('sess-push'))
  await pollsLanded(2)
  rig.lagRelease = true

  await act(async () => { await runSignOutTasks() })
  await pollsLanded(2)

  window.dispatchEvent(new Event('pagehide'))
  expect(deviceStopOnUnload).toHaveBeenCalledWith('camera')
}, 30_000)
