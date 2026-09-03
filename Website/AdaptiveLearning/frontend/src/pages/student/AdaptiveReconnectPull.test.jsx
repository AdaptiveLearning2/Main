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
  // Recorded, so the session count moves and a goal can be reached.
  recordAnswer: vi.fn(async () => ({ topic: 'expressions' })),
}))

// The bridge as the backend relays it under pull, via /api/eeg/status.
// `stamps` records when each status read happened: the two polls are
// phase-locked to the first one, and the test times the drop off it.
// `pollerRunning` follows the recorder, as the backend's poller follows
// /api/eeg/start and /stop: under pull that is what `connected` reads.
const bridge = { ingestion: {}, stamps: [], recorders: [], pollerRunning: false }
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

const CONNECTED = { muse_connected: true, muse_devices: ['Muse-1'], battery_percent: 80,
                    auto_reconnect: true, reconnecting: false, reconnect_attempt: 0,
                    reconnect_max_attempts: 5, reconnect_exhausted: false }

beforeEach(() => {
  resetApi()
  vi.clearAllMocks()
  bridge.ingestion = { ...CONNECTED }
  bridge.stamps = []
  bridge.recorders = []
  bridge.pollerRunning = false
  bridge.sessions = 0
  const question = () => ({
    question_text: 'What is 2 + 2?', answer_options: ['3', '4'], correct_answer: '4',
    subject: 'expressions', difficulty: 'easy',
  })
  mockApi({
    'GET /api/generate-question?user_id=u1&bias=0&grade=4th+Grade&session_id=sess-1': question,
    'GET /api/generate-question?user_id=u1&bias=0&grade=4th+Grade&session_id=sess-2': question,
    'GET /api/profile/me': () => ({ id: 'u1', role: 'student', grade_level: '4th Grade' }),
    'GET /api/classes': () => [],
    'GET /api/performance/student/u1': () => [],
    // A new id per session, so a second sitting is a second session.
    'POST /api/sessions/start': () => ({ id: `sess-${++bridge.sessions}` }),
    'POST /api/eeg/muse/disconnect': () => ({ ok: true }),
    'POST /api/eeg/muse/refresh': () => ({ ok: true }),
    'POST /api/eeg/muse/connect': () => ({ ok: true }),
  })
})

afterEach(() => cleanup())

it('brings the stream up at Connect and records only from the first question', async () => {
  // Found on hardware: a student who paired and never started a question had
  // rows on the teacher's Live view. Pairing needs the poller; a lesson is
  // what should be recorded.
  render(<Adaptive />)
  const button = await screen.findByRole('button', { name: /connect headband/i })
  await waitFor(() => expect(button).not.toBeDisabled())
  fireEvent.click(button)
  await screen.findByText(/STREAMING/, {}, { timeout: 10000 })
  // Let the pairing finish before the test does: `pairOnce` is not cancelled
  // by an unmount, and a scan it sends after this test ends is counted by
  // the next one.
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

it('stops the finished session\'s recorder before starting the next one\'s', async () => {
  // Each recorder registers a `beforeunload` listener at construction that
  // only its `stop()` removes. `armRecording` is the one place a live
  // recorder is replaced -- after a Finish, for the next session -- and it
  // used to drop the old one without stopping it, leaving a listener per
  // Finish-and-resume, all posting /api/eeg/stop for long-ended sessions on
  // tab close.
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
  // This harness's `useAuth` returns a fresh `user` literal on every call,
  // which is what a real token refresh does too. Keyed on the object, the
  // performance read re-ran on every status tick: 172 requests in 7s.
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
  // The bridge reports no headband until the scan and connect have run --
  // the ordinary shape of every pairing, and one the 5s poll observes at
  // least once, since `connected` is true from `/api/eeg/start`.
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
  // One scan and one connect: nothing sent a second pairing over the first.
  expect(apiFetch.mock.calls.filter(c => c[0] === '/api/eeg/muse/refresh')).toHaveLength(1)
  expect(apiFetch.mock.calls.filter(c => c[0] === '/api/eeg/muse/connect')).toHaveLength(1)
  expect(screen.getByText(/STREAMING/)).toBeInTheDocument()
}, 60_000)

it('stays disconnected after giving up, under pull where the poller would otherwise say streaming', async () => {
  // Seen on hardware: the bridge exhausted, the page's three attempts found
  // nothing, the toast showed -- and three seconds later the panel read
  // STREAMING with a Disconnect button, because the give-up path reset the
  // state without stopping the poller, and under pull `connected` *is* the
  // poller. The teardown has to be Disconnect's.
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

  // Past the next status tick, and the next.
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
