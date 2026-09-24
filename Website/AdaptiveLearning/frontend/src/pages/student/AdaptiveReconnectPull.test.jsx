/** Reconnect under `pull`, where the 3s status poll and the 5s telemetry poll both read the bridge. */
import { it, expect, beforeEach, afterEach, vi } from 'vitest'
import { render, screen, cleanup, fireEvent, waitFor } from '@testing-library/react'

vi.mock('../../lib/api', async () => await import('../../test/mocks/apiFetch'))
vi.mock('../../lib/supabase', async () => await import('../../test/mocks/supabase'))
vi.mock('sonner', () => ({
  toast: { error: vi.fn(), success: vi.fn(), warning: vi.fn() },
}))
vi.mock('../../lib/session', () => ({
  endSession: vi.fn(async () => true),
  // Recorded, so the session count moves and a goal can be reached.
  recordAnswer: vi.fn(async () => ({ topic: 'expressions' })),
}))

// The bridge as relayed by /api/eeg/status; `stamps` times each read, `pollerRunning` follows the recorder.
const bridge = { ingestion: {}, stamps: [], recorders: [], pollerRunning: false,
                 // `eegStatus` swallows failure into a shaped object; this is an unlanded read.
                 unanswered: false }
vi.mock('../../lib/signals', () => ({
  createSignalRecorder: ({ sessionId }) => {
    const rec = {
      sessionId,
      start: vi.fn(async () => { bridge.pollerRunning = true; return { ok: true, running: true } }),
      stop: vi.fn(async () => { bridge.pollerRunning = false }),
    }
    bridge.recorders.push(rec)
    return rec
  },
  eegHealth: vi.fn(async () => ({ available: true, ingest_mode: 'pull' })),
  eegStatus: vi.fn(async () => {
    bridge.stamps.push(Date.now())
    if (bridge.unanswered) return { answered: false, service: false, poller: { running: false } }
    return {
      ingest_mode: 'pull', service: true,
      poller: { running: bridge.pollerRunning, samples: 3 },
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

// `eeg_age_ms` is required for "connected": a link counts only with EEG flowing.
const CONNECTED = { muse_connected: true, muse_devices: ['Muse-1'], battery_percent: 80,
                    auto_reconnect: true, reconnecting: false, reconnect_attempt: 0,
                    reconnect_max_attempts: 5, reconnect_exhausted: false, eeg_age_ms: 2 }

beforeEach(() => {
  resetApi()
  vi.clearAllMocks()
  // Discoverable, not yet connected; the connect route flips it.
  bridge.ingestion = { ...CONNECTED, muse_connected: false }
  bridge.stamps = []
  bridge.recorders = []
  bridge.pollerRunning = false
  bridge.unanswered = false
  bridge.sessions = 0
  const question = () => ({
    question_text: 'What is 2 + 2?', answer_options: ['3', '4'], correct_answer: '4',
    subject: 'expressions', difficulty: 'easy',
  })
  mockApi({
    'GET /api/generate-question?bias=0&grade=4th+Grade&session_id=sess-1': question,
    'GET /api/generate-question?bias=0&grade=4th+Grade&session_id=sess-2': question,
    'GET /api/profile/me': () => ({ id: 'u1', role: 'student', grade_level: '4th Grade' }),
    'GET /api/classes': () => [],
    'GET /api/performance/student/u1': () => [],
    // A new id per session.
    'POST /api/sessions/start': () => ({ id: `sess-${++bridge.sessions}` }),
    'POST /api/eeg/muse/disconnect': () => ({ ok: true }),
    'POST /api/eeg/muse/refresh': () => ({ ok: true }),
    'POST /api/eeg/muse/connect': () => { bridge.ingestion = { ...bridge.ingestion, muse_connected: true }; return { ok: true } },
  })
})

afterEach(() => cleanup())

it('brings the stream up at Connect and records only from the first question', async () => {
  // Pairing needs the poller; only a lesson is recorded.
  render(<Adaptive />)
  const button = await screen.findByRole('button', { name: /connect headband/i })
  await waitFor(() => expect(button).not.toBeDisabled())
  fireEvent.click(button)
  await screen.findByText(/STREAMING/, {}, { timeout: 10000 })
  // `pairOnce` survives unmount, so let it finish or its scan counts in the next test.
  await waitFor(() => expect(apiFetch.mock.calls.some(c => c[0] === '/api/eeg/muse/connect')).toBe(true),
                { timeout: 10000 })
  await new Promise(r => setTimeout(r, 1500))
  expect(bridge.recorders).toHaveLength(1)
  expect(bridge.recorders[0].start).toHaveBeenCalledWith({ record: false })
  expect(bridge.recorders[0].start).not.toHaveBeenCalledWith({ record: true })

  fireEvent.click(screen.getByRole('button', { name: /generate question/i }))
  await screen.findByText(/What is 2 \+ 2\?/)
  // Same session as the pairing, so the same recorder is armed in place.
  expect(bridge.recorders).toHaveLength(1)
  await waitFor(() => expect(bridge.recorders[0].start).toHaveBeenCalledWith({ record: true }))
}, 30_000)

/**
 * The duration clock starts at the first question, not at Connect.
 * Real timers: the reminder checks every 20 s, so each wait is 22 s.
 */
it('starts the duration clock at the first question, not at Connect', async () => {
  mockApi({
    'GET /api/generate-question?bias=0&grade=4th+Grade&session_id=sess-1': () => ({
      question_text: 'What is 2 + 2?', answer_options: ['3', '4'], correct_answer: '4',
      subject: 'expressions', difficulty: 'easy',
    }),
    'GET /api/profile/me': () => ({ id: 'u1', role: 'student', grade_level: '4th Grade',
                                    session_duration_minutes: 0.001 }),
    'GET /api/classes': () => [],
    'GET /api/performance/student/u1': () => [],
    'POST /api/sessions/start': () => ({ id: `sess-${++bridge.sessions}` }),
    'POST /api/eeg/muse/disconnect': () => ({ ok: true }),
    'POST /api/eeg/muse/refresh': () => ({ ok: true }),
    'POST /api/eeg/muse/connect': () => { bridge.ingestion = { ...bridge.ingestion, muse_connected: true }; return { ok: true } },
  })
  render(<Adaptive />)
  const button = await screen.findByRole('button', { name: /connect headband/i })
  await waitFor(() => expect(button).not.toBeDisabled())
  fireEvent.click(button)
  await screen.findByText(/STREAMING/, {}, { timeout: 10000 })
  await waitFor(() => expect(apiFetch.mock.calls.some(c => c[0] === '/api/eeg/muse/connect')).toBe(true),
                { timeout: 10000 })

  // Paired and past a tick, but no question asked yet.
  await new Promise(r => setTimeout(r, 22000))
  expect(screen.queryByText(/That is your 0\.001 minutes/)).toBeNull()

  fireEvent.click(screen.getByRole('button', { name: /generate question/i }))
  await screen.findByText(/What is 2 \+ 2\?/)
  // Without this half, a clock that never started would pass.
  await new Promise(r => setTimeout(r, 22000))
  expect(screen.getByText(/That is your 0\.001 minutes/)).toBeInTheDocument()
}, 90_000)

it('stops the finished session\'s recorder before starting the next one\'s', async () => {
  // Each recorder's `beforeunload` listener is removed only by its `stop()`.
  render(<Adaptive />)
  const button = await screen.findByRole('button', { name: /connect headband/i })
  await waitFor(() => expect(button).not.toBeDisabled())
  fireEvent.click(button)
  await screen.findByText(/STREAMING/, {}, { timeout: 10000 })
  await waitFor(() => expect(apiFetch.mock.calls.some(c => c[0] === '/api/eeg/muse/connect')).toBe(true),
                { timeout: 10000 })
  await new Promise(r => setTimeout(r, 1500))

  // A goal of five, then five answers, reaches the Finish banner.
  fireEvent.click(screen.getByRole('button', { name: '5' }))
  for (let i = 0; i < 5; i++) {
    fireEvent.click(screen.getByRole('button', { name: i === 0 ? /generate question/i : /next question/i }))
    await screen.findByText(/What is 2 \+ 2\?/)
    // Option B is "4"; the letter and the value are two spans in one button.
    fireEvent.click(screen.getByRole('button', { name: /^B\s?4/ }))
    fireEvent.click(screen.getByRole('button', { name: /submit answer/i }))
    await screen.findByRole('button', { name: /next question/i })
  }
  fireEvent.click(await screen.findByRole('button', { name: /finish session/i }))
  await screen.findByRole('button', { name: /generate question/i })
  expect(bridge.recorders).toHaveLength(1)

  fireEvent.click(screen.getByRole('button', { name: /generate question/i }))
  await screen.findByText(/What is 2 \+ 2\?/)
  await waitFor(() => expect(bridge.recorders).toHaveLength(2))
  expect(bridge.recorders[0].sessionId).toBe('sess-1')
  expect(bridge.recorders[0].stop).toHaveBeenCalledTimes(1)
  expect(bridge.recorders[1].sessionId).toBe('sess-2')
  await waitFor(() => expect(bridge.recorders[1].start).toHaveBeenCalledWith({ record: true }))
}, 30_000)

it('abandons a pairing when the page unmounts, instead of scanning for a page that is gone', async () => {
  const { unmount } = render(<Adaptive />)
  const button = await screen.findByRole('button', { name: /connect headband/i })
  await waitFor(() => expect(button).not.toBeDisabled())
  fireEvent.click(button)
  // Inside the 1.5s settle between the disconnect and the scan.
  await waitFor(() => expect(apiFetch.mock.calls.some(c => c[0] === '/api/eeg/muse/disconnect')).toBe(true))
  unmount()
  await new Promise(r => setTimeout(r, 3000))
  expect(apiFetch.mock.calls.filter(c => c[0] === '/api/eeg/muse/refresh')).toHaveLength(0)
  expect(apiFetch.mock.calls.filter(c => c[0] === '/api/eeg/muse/connect')).toHaveLength(0)
}, 20_000)

it('reads topic performance once, not once per render', async () => {
  // `useAuth` here returns a fresh `user` object each call, as a token refresh does.
  render(<Adaptive />)
  const button = await screen.findByRole('button', { name: /connect headband/i })
  await waitFor(() => expect(button).not.toBeDisabled())
  fireEvent.click(button)
  await screen.findByText(/STREAMING/, {}, { timeout: 10000 })
  await new Promise(r => setTimeout(r, 3500))
  expect(apiFetch.mock.calls.filter(c => c[0] === '/api/performance/student/u1').length).toBeLessThanOrEqual(2)
  // Let the pairing finish rather than leak into the next test.
  await waitFor(() => expect(apiFetch.mock.calls.some(c => c[0] === '/api/eeg/muse/connect')).toBe(true),
                { timeout: 10000 })
  await new Promise(r => setTimeout(r, 1500))
}, 30_000)

it('does not read the pairing itself as a drop, which under pull starts with connected: true', async () => {
  // No headband until scan and connect run, while `connected` is already true from `/api/eeg/start`.
  bridge.ingestion = { ...CONNECTED, muse_connected: false, active_muse_name: '', battery_percent: null }
  const flip = setTimeout(() => { bridge.ingestion = { ...CONNECTED } }, 3500)
  render(<Adaptive />)
  const button = await screen.findByRole('button', { name: /connect headband/i })
  await waitFor(() => expect(button).not.toBeDisabled())
  fireEvent.click(button)
  await new Promise(r => setTimeout(r, 6500))
  clearTimeout(flip)

  expect(toast.warning).not.toHaveBeenCalled()
  expect(screen.queryByText(/reconnecting/i)).toBeNull()
  // One scan and one connect.
  expect(apiFetch.mock.calls.filter(c => c[0] === '/api/eeg/muse/refresh')).toHaveLength(1)
  expect(apiFetch.mock.calls.filter(c => c[0] === '/api/eeg/muse/connect')).toHaveLength(1)
  expect(screen.getByText(/STREAMING/)).toBeInTheDocument()
}, 60_000)

it('does not scan on a status read that never landed, which would drop a live link', async () => {
  // An unlanded read must not reach `pairOnce` (which disconnects first); observed via the scan count.
  render(<Adaptive />)
  const button = await screen.findByRole('button', { name: /connect headband/i })
  await waitFor(() => expect(button).not.toBeDisabled())
  fireEvent.click(button)
  await screen.findByText(/STREAMING/, {}, { timeout: 10000 })
  await waitFor(() => expect(apiFetch.mock.calls.some(c => c[0] === '/api/eeg/muse/connect')).toBe(true),
                { timeout: 10000 })
  const scansAfterPairing = apiFetch.mock.calls.filter(c => c[0] === '/api/eeg/muse/refresh').length

  // The drop must be observed before reads stop, or no reconnect loop starts.
  await new Promise(r => setTimeout(r, 1500))
  bridge.ingestion = { ...CONNECTED, muse_connected: false, reconnecting: false,
                       reconnect_exhausted: true, muse_devices: [], battery_percent: null }
  await screen.findByText(/reconnecting \(attempt 1 of 3\)/, {}, { timeout: 12000 })

  // From here every read answers nothing.
  bridge.unanswered = true

  // Past the 2s and 4s backoffs, so the loop has reached its pairing point twice.
  await new Promise(r => setTimeout(r, 9000))
  expect(apiFetch.mock.calls.filter(c => c[0] === '/api/eeg/muse/refresh'))
    .toHaveLength(scansAfterPairing)
}, 60_000)

it('stays disconnected after giving up, under pull where the poller would otherwise say streaming', async () => {
  // Under pull `connected` is the poller, so giving up must stop it as Disconnect does.
  render(<Adaptive />)
  const button = await screen.findByRole('button', { name: /connect headband/i })
  await waitFor(() => expect(button).not.toBeDisabled())
  fireEvent.click(button)
  await screen.findByText(/STREAMING/, {}, { timeout: 10000 })
  await waitFor(() => expect(apiFetch.mock.calls.some(c => c[0] === '/api/eeg/muse/connect')).toBe(true),
                { timeout: 10000 })
  await new Promise(r => setTimeout(r, 1500))

  // The bridge has given up and no headband is in the scan.
  bridge.ingestion = { ...CONNECTED, muse_connected: false, reconnecting: false,
                       reconnect_exhausted: true, muse_devices: [], battery_percent: null }
  await screen.findByText(/reconnecting \(attempt 1 of 3\)/, {}, { timeout: 9000 })
  // Three attempts: 2/4/8s backoffs, each followed by a 12s scan that finds nothing.
  await screen.findByText(/could not be reconnected|Connect headband/i, {}, { timeout: 60000 })
  await waitFor(() => expect(toast.error).toHaveBeenCalledWith('The headband could not be reconnected.', expect.anything()),
                { timeout: 5000 })

  // Past the next two status ticks.
  await new Promise(r => setTimeout(r, 7000))
  expect(screen.queryByText(/STREAMING/)).toBeNull()
  expect(screen.getByRole('button', { name: /connect headband/i })).toBeInTheDocument()
  expect(bridge.recorders[0].stop).toHaveBeenCalled()
}, 120_000)

it('announces a drop under pull, where the poller keeps running through it', async () => {
  render(<Adaptive />)
  const button = await screen.findByRole('button', { name: /connect headband/i })
  await waitFor(() => expect(button).not.toBeDisabled())
  fireEvent.click(button)
  // Wait for pairing to complete, or its last poll reads the restore as a first connection.
  await screen.findByText(/STREAMING/, {}, { timeout: 10000 })
  await waitFor(() => expect(apiFetch.mock.calls.some(c => c[0] === '/api/eeg/muse/connect')).toBe(true),
                { timeout: 10000 })

  // The drop must land where the 3s status tick precedes the 5s telemetry tick; keyed to the recorded 5s read.
  const t0 = bridge.stamps[0]
  await waitFor(() => expect(bridge.stamps.some(t => t >= t0 + 4900)).toBe(true),
                { timeout: 8000 })

  bridge.ingestion = { ...CONNECTED, muse_connected: false, reconnecting: true,
                       reconnect_attempt: 1, battery_percent: null }
  await screen.findByText(/reconnecting \(attempt 1 of 5\)/, {}, { timeout: 9000 })
  expect(toast.warning).toHaveBeenCalledWith('The headband disconnected.', expect.anything())
  expect(screen.getByRole('button', { name: /stop trying/i })).toBeInTheDocument()
  // The page waited for the bridge rather than scanning itself.
  expect(apiFetch.mock.calls.filter(c => c[0] === '/api/eeg/muse/refresh')).toHaveLength(1)

  bridge.ingestion = { ...CONNECTED }
  await screen.findByText(/STREAMING/, {}, { timeout: 6000 })
  expect(toast.success).toHaveBeenCalledWith('Headband reconnected.')
}, 60_000)


it('does not spend the reconnect budget on attempts that reached nothing', async () => {
  // A `status_unavailable` abort must not cost an attempt, nor end the telemetry watch.
  render(<Adaptive />)
  const button = await screen.findByRole('button', { name: /connect headband/i })
  await waitFor(() => expect(button).not.toBeDisabled())
  fireEvent.click(button)
  await screen.findByText(/STREAMING/, {}, { timeout: 10000 })
  await waitFor(() => expect(apiFetch.mock.calls.some(c => c[0] === '/api/eeg/muse/connect')).toBe(true),
                { timeout: 10000 })
  await new Promise(r => setTimeout(r, 1500))

  // Bridge not retrying, so the page's loop drives; the drop must be observed first.
  bridge.ingestion = { ...CONNECTED, muse_connected: false, reconnecting: false,
                       reconnect_exhausted: true, muse_devices: [], battery_percent: null }
  await screen.findByText(/reconnecting \(attempt 1 of 3\)/, {}, { timeout: 9000 })

  // Now the server stops answering.
  bridge.unanswered = true

  // Past 2+4+8s, the whole budget if every attempt aborts immediately.
  await new Promise(r => setTimeout(r, 25000))
  expect(toast.error).not.toHaveBeenCalledWith('The headband could not be reconnected.', expect.anything())
  expect(screen.getByRole('button', { name: /stop trying/i })).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: /connect headband/i })).toBeNull()
  expect(bridge.recorders[0].stop).not.toHaveBeenCalled()

  // The link returns with the server and is picked up without a click.
  bridge.ingestion = { ...CONNECTED }
  bridge.unanswered = false
  await screen.findByText(/STREAMING/, {}, { timeout: 10000 })
  expect(toast.success).toHaveBeenCalledWith('Headband reconnected.')
}, 120_000)


it('stops counting attempts once no attempt is running', async () => {
  // While waiting on an unreachable server, "attempt 1 of 3" names a bound nothing will reach.
  render(<Adaptive />)
  const button = await screen.findByRole('button', { name: /connect headband/i })
  await waitFor(() => expect(button).not.toBeDisabled())
  fireEvent.click(button)
  await screen.findByText(/STREAMING/, {}, { timeout: 10000 })
  await waitFor(() => expect(apiFetch.mock.calls.some(c => c[0] === '/api/eeg/muse/connect')).toBe(true),
                { timeout: 10000 })
  await new Promise(r => setTimeout(r, 1500))

  bridge.ingestion = { ...CONNECTED, muse_connected: false, reconnecting: false,
                       reconnect_exhausted: true, muse_devices: [], battery_percent: null }
  await screen.findByText(/reconnecting \(attempt 1 of 3\)/, {}, { timeout: 9000 })
  bridge.unanswered = true

  // Generous: the aborting read may be a full scan cycle away.
  const line = await screen.findByText(/server can't be reached/, {}, { timeout: 25000 })
  expect(line).toHaveTextContent(/disconnected/)
  expect(screen.queryByText(/attempt \d+ of \d+/)).toBeNull()
}, 90_000)
