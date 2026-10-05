/** Readings the backend accepts but declines to record are named on the lesson page, never shown as RECORDING. */
import { it, expect, beforeEach, afterEach, vi } from 'vitest'
import { render, screen, cleanup, fireEvent, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

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
// The bridge as the sidecar reports it; one test starts it already holding a headband.
const bridge = { running: false, ingestion: {} }
vi.mock('../../lib/sidecar', () => ({
  startPush: vi.fn(async () => ({})), stopPush: vi.fn(async () => ({})),
  stopPushOnUnload: vi.fn(),
  pushStatus: vi.fn(async () => ({ enabled: true, running: true, recorded: {} })),
  deviceStart: vi.fn(async () => ({})), deviceStop: vi.fn(async () => ({})),
  deviceStopOnUnload: vi.fn(),
  museRefresh: vi.fn(async () => ({})),
  museConnect: vi.fn(async () => ({})),
  museDisconnect: vi.fn(async () => ({})),
  museState: vi.fn(async () => ({ running: bridge.running, ingestion: { ...bridge.ingestion } })),
  devices: vi.fn(async () => [
    { device_id: 'default', kind: 'muse', running: false },
    { device_id: 'camera', kind: 'face', running: true },
  ]),
  releasePushIfIdle: vi.fn(async () => ({ stopped: true, devices: [] })),
  sidecarDebug: vi.fn(async () => ({})),
  sidecarState: vi.fn(async () => null),
  debugFaceFrame: vi.fn(async () => null),
}))
vi.mock('../../context/AuthContext', () => ({
  useAuth: () => ({ user: { id: 'u1', email: 'a@b.c' }, role: 'student', loading: false }),
}))

import { pushStatus } from '../../lib/sidecar'
import { mockApi, resetApi } from '../../test/mocks/apiFetch'
import { buildRecordingPermits } from '../../test/fixtures/recordingPermits'
import Adaptive from './Adaptive'

// The receipts of a student with no consent on any sensor, as the sidecar reports them.
const ALL_DECLINED = {
  enabled: true, running: true, recorded: { cognitive: 0, heart: 0, face: 0 },
  last_result: { cognitive: 'declined', heart: 'declined', face: 'declined' },
  declined_reason: { cognitive: 'eeg not consented', heart: 'no consented heart sensor',
                     face: 'camera not consented' },
}
// `eeg_age_ms` is required for "connected": a link counts only with EEG flowing.
const CONNECTED = { muse_connected: true, muse_devices: ['Muse-1'], battery_percent: 80,
                    auto_reconnect: true, reconnecting: false, reconnect_attempt: 0,
                    reconnect_max_attempts: 5, reconnect_exhausted: false, eeg_age_ms: 2,
                    active_muse_name: 'Muse-1' }

beforeEach(() => {
  resetApi()
  vi.clearAllMocks()
  bridge.running = false
  bridge.ingestion = {}
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

/** The panel a card's title heads: its chips and its sentences. */
const panelOf = (title) => screen.getByText(title).closest('div')

/** Render and start a session, which is what starts the push status poll. */
async function renderInSession(status) {
  pushStatus.mockImplementation(async () => status)
  render(<Adaptive />)
  await userEvent.click(await screen.findByRole('button', { name: /generate question/i }))
  await screen.findByText('What is 2 + 2?')
}

it('names what the backend is not saving, and why, instead of claiming RECORDING', async () => {
  await renderInSession(ALL_DECLINED)

  expect(await screen.findByText(/Not being saved:/)).toHaveTextContent(
    'Headband (eeg not consented) · Heart sensor (no consented heart sensor) · Camera (camera not consented)')
  const headband = panelOf('Muse Headband')
  expect(within(headband).getByText('not recording')).toBeInTheDocument()
  expect(within(headband).queryByText('● RECORDING')).toBeNull()
})

it('keeps RECORDING for the channels being saved and names only the one that is not', async () => {
  await renderInSession({
    ...ALL_DECLINED,
    last_result: { cognitive: 'recorded', heart: 'recorded', face: 'declined' },
    declined_reason: { cognitive: null, heart: null, face: 'camera not consented' },
  })

  expect(await screen.findByText(/Not being saved:/)).toHaveTextContent(
    /^⚠ Not being saved: Camera \(camera not consented\)$/)
  expect(within(panelOf('Muse Headband')).getByText('● RECORDING')).toBeInTheDocument()
  const camera = panelOf('Camera')
  expect(within(camera).getByText('on, not recording')).toBeInTheDocument()
  expect(within(camera).queryByText('● RECORDING')).toBeNull()
  expect(within(camera).getByText(/nothing from it is being saved \(camera not consented\)/)).toBeInTheDocument()
})

it('claims no RECORDING when nothing was saved lately, as in a backend outage', async () => {
  // Results go stale after 30 s without a receipt; the sidecar backs off for up to 120 s.
  await renderInSession({
    enabled: true, running: true, recorded: { cognitive: 40, heart: 3, face: 40 },
    last_result: { cognitive: null, heart: null, face: null }, declined_reason: {},
  })

  const headband = panelOf('Muse Headband')
  expect(await within(headband).findByText('not recording')).toBeInTheDocument()
  expect(within(headband).queryByText('● RECORDING')).toBeNull()
  const camera = panelOf('Camera')
  expect(within(camera).getByText('on, not recording')).toBeInTheDocument()
  expect(within(camera).queryByText('● RECORDING')).toBeNull()
  // The sentence beside the badge says the same thing, not "Reading how you are finding the questions".
  expect(within(camera).getByText(/nothing from it has been saved recently/)).toBeInTheDocument()
  expect(screen.queryByText(/Not being saved/)).toBeNull()
})

it('waits neutrally, rather than saying "not recording", before anything has come back', async () => {
  await renderInSession({
    enabled: true, running: true, recorded: { cognitive: 0, heart: 0, face: 0 },
    declined: { cognitive: 0, heart: 0, face: 0 },
    last_result: { cognitive: null, heart: null, face: null }, declined_reason: {},
  })

  // Only a status sets `recorded`, so this line proves the status has landed.
  await screen.findByText('0 readings recorded from this computer.')
  const camera = panelOf('Camera')
  expect(within(camera).getByText('on, waiting to save')).toBeInTheDocument()
  expect(within(camera).getByText(/Waiting for its first readings to be saved/)).toBeInTheDocument()
  // No headband is connected, so its card has nothing to wait on and claims nothing.
  const headband = panelOf('Muse Headband')
  expect(within(headband).queryByText('waiting to save')).toBeNull()
  expect(within(headband).queryByText('not recording')).toBeNull()
  expect(within(headband).queryByText('● RECORDING')).toBeNull()
})

it('keeps the headband card off RECORDING when only the camera is being saved', async () => {
  await renderInSession({
    ...ALL_DECLINED,
    last_result: { cognitive: 'declined', heart: 'declined', face: 'recorded' },
    declined_reason: { cognitive: 'eeg not consented', heart: 'no consented heart sensor', face: null },
  })

  expect(await screen.findByText(/Not being saved:/)).toHaveTextContent('Headband (eeg not consented)')
  const headband = panelOf('Muse Headband')
  expect(within(headband).getByText('not recording')).toBeInTheDocument()
  expect(within(headband).queryByText('● RECORDING')).toBeNull()
  expect(within(panelOf('Camera')).getByText('● RECORDING')).toBeInTheDocument()
})

it('claims nothing for the camera before the sidecar has answered', async () => {
  pushStatus.mockImplementation(() => new Promise(() => {}))
  render(<Adaptive />)
  await userEvent.click(await screen.findByRole('button', { name: /generate question/i }))
  await screen.findByText('What is 2 + 2?')

  const camera = panelOf('Camera')
  expect(within(camera).getByText('on, waiting to save')).toBeInTheDocument()
  expect(within(camera).queryByText('● RECORDING')).toBeNull()
})

it('reads a channel whose readings were only ever declined, and then went quiet, as stale', async () => {
  await renderInSession({
    enabled: true, running: true, recorded: { cognitive: 0, heart: 0, face: 0 },
    declined: { cognitive: 12, heart: 0, face: 0 },
    last_result: { cognitive: null, heart: null, face: null }, declined_reason: {},
  })

  const headband = panelOf('Muse Headband')
  expect(await within(headband).findByText('not recording')).toBeInTheDocument()
  expect(within(headband).queryByText('waiting to save')).toBeNull()
})

it('reads an older sidecar, which sends no last result, as recording', async () => {
  await renderInSession({ enabled: true, running: true, recorded: { cognitive: 5 } })

  expect(await within(panelOf('Muse Headband')).findByText('● RECORDING')).toBeInTheDocument()
  expect(screen.queryByText(/Not being saved/)).toBeNull()
})

/** Adopt a headband the bridge already holds, then start a session under `status`. */
async function connectInSession(status) {
  bridge.running = true
  bridge.ingestion = { ...CONNECTED }
  pushStatus.mockImplementation(async () => status)
  render(<Adaptive />)
  const button = await screen.findByRole('button', { name: /connect headband/i })
  await waitFor(() => expect(button).not.toBeDisabled())
  fireEvent.click(button)
  await screen.findByText(/STREAMING/, {}, { timeout: 10000 })
  await userEvent.click(screen.getByRole('button', { name: /generate question/i }))
}

it('stops telling a connected student their teacher can see them when the headband is not saved', async () => {
  await connectInSession(ALL_DECLINED)

  expect(await screen.findByText(/not being saved, so your teacher cannot see them/)).toBeInTheDocument()
  expect(screen.queryByText(/teacher can see your focus/)).toBeNull()
}, 60_000)

it('waits neutrally on a connected headband before its readings come back', async () => {
  await connectInSession({
    enabled: true, running: true, recorded: { cognitive: 0, heart: 0, face: 0 },
    declined: { cognitive: 0, heart: 0, face: 0 },
    last_result: { cognitive: null, heart: null, face: null }, declined_reason: {},
  })

  expect(await within(panelOf('Muse Headband')).findByText('waiting to save')).toBeInTheDocument()
  expect(screen.getByText('Connected. Waiting for the first readings to be saved.')).toBeInTheDocument()
  expect(screen.queryByText(/teacher can see your focus/)).toBeNull()
}, 60_000)

it('stops telling a connected student their teacher can see them when nothing was saved lately', async () => {
  await connectInSession({
    enabled: true, running: true, recorded: { cognitive: 30, heart: 0, face: 0 },
    last_result: { cognitive: null, heart: null, face: null }, declined_reason: {},
  })

  expect(await screen.findByText(/no readings have been saved recently/)).toBeInTheDocument()
  expect(screen.queryByText(/teacher can see your focus/)).toBeNull()
}, 60_000)
