/**
 * A refused health probe is not a headband that is offline.
 *
 * `/api/eeg/health` is polled every 5 s per open lesson, which makes it the
 * heaviest caller of any per-address rate limit on the public routes -- and
 * `eegHealth` used to fold every failure into `available: false`. A limit
 * reached anywhere near that endpoint therefore reported the *hardware* as
 * down, on every student's page at once, with nothing saying a limit caused
 * it. `apiFetch` retries only 503, so nothing upstream softened it either.
 *
 * Three states, not two: reachable, not reachable, and a probe that answered
 * neither.
 */
import { it, expect, beforeEach, vi } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

vi.mock('../../lib/api', async () => await import('../../test/mocks/apiFetch'))
vi.mock('../../lib/supabase', async () => await import('../../test/mocks/supabase'))
vi.mock('sonner', () => ({ toast: { error: vi.fn(), success: vi.fn() } }))
vi.mock('../../lib/session', () => ({
  endSession: vi.fn(async () => true),
  recordAnswer: vi.fn(async () => ({ topic: 'ordering' })),
}))
vi.mock('../../lib/signals', () => ({
  createSignalRecorder: () => ({ start: vi.fn(), stop: vi.fn() }),
  eegHealth: vi.fn(async () => ({ available: true, ingest_mode: 'pull' })),
  eegStatus: vi.fn(async () => ({})),
  eegDevices: vi.fn(async () => ({ devices: [] })),
}))
// Everything the page imports from here, not only what the pull tests reach:
// the push test below takes branches that call the rest, and a factory missing
// one of them fails where the double is thin rather than where a bug is.
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

import { mockApi, overrideApi, resetApi } from '../../test/mocks/apiFetch'
import { eegHealth, eegStatus, eegDevices } from '../../lib/signals'
import { museState, devices } from '../../lib/sidecar'
import Adaptive from './Adaptive'

beforeEach(() => {
  resetApi()
  vi.clearAllMocks()
  mockApi({
    'GET /api/profile/me': () => ({ id: 'u1', role: 'student', grade_level: '1st Grade' }),
    'GET /api/performance/student/u1': () => [],
    'POST /api/sessions/start': () => ({ id: 'sess-probe' }),
    'GET /api/eeg/health': () => ({ available: true, ingest_mode: 'pull' }),
    'GET /api/eeg/status': () => ({}),
  })
})

it('says the check failed, not that the headband is offline', async () => {
  // Refused from the first probe, so nothing has ever answered: `available` is
  // null here, not false, and the page may not claim an outage it has never
  // observed any more than it may claim the service is fine.
  eegHealth.mockResolvedValue({ refused: true, error: 'Too many requests. Slow down.' })
  render(<Adaptive />)

  expect(await screen.findByText('status unavailable')).toBeInTheDocument()
  // The word this replaces. "offline" is a claim about the headband, and a
  // refused probe has not earned it.
  expect(screen.queryByText('offline')).not.toBeInTheDocument()

  // The sentence under the badge, which is a second copy of the same claim and
  // needs a query that can see it: the badge assertions above match whole text
  // nodes of two words, so they read as covering this panel while saying
  // nothing about the paragraph beside them.
  const sentence = await screen.findByText(/EEG service/)
  expect(sentence).toHaveTextContent(/Could not check/)
  // Worse than the badge it replaced: naming a layer and a port sends a
  // student to restart a backend that answered perfectly well.
  expect(sentence).not.toHaveTextContent(/port 8001/)
  expect(sentence).not.toHaveTextContent(/backend is running/)
})

it('goes on acting on the last answer without claiming it is current', async () => {
  // Two halves, and the first is why the state is kept stale at all: station
  // discovery and the Connect button are both gated on `available`, so
  // overwriting it with false during a refusal would take a working headband
  // off the page.
  //
  // The second is what the badge may say about that. "ready" is a claim that
  // the sidecar was reachable, and a refused probe has confirmed nothing -- so
  // the page keeps *behaving* as though the last answer holds while saying out
  // loud that it could not check. Asserting only "ready is still there" would
  // pass with both badges on screen at once, which is the one state this
  // change exists for and the one it must not contradict itself in.
  eegHealth.mockResolvedValueOnce({ available: true, ingest_mode: 'pull' })
  eegHealth.mockResolvedValue({ refused: true, error: 'Too many requests.' })
  render(<Adaptive />)

  await screen.findByText('ready')
  await screen.findByText('status unavailable', undefined, { timeout: 8000 })

  // Still acting on it: station discovery is gated on `available` and tears
  // itself down when that goes false, so calls continuing past the refusal is
  // the behaviour, not a badge asserting itself.
  const during = eegDevices.mock.calls.length
  await waitFor(() => expect(eegDevices.mock.calls.length).toBeGreaterThan(during),
                { timeout: 8000 })

  // And exactly one of the three badges is on screen.
  expect(screen.getByText('status unavailable')).toBeInTheDocument()
  expect(screen.queryByText('ready')).not.toBeInTheDocument()
  expect(screen.queryByText('offline')).not.toBeInTheDocument()
  // The sentence has to follow the badge. This is the state where it read
  // "EEG service ready" -- the suppressed claim verbatim, and stronger for
  // being a sentence rather than a two-word chip.
  expect(screen.getByText(/EEG service/)).toHaveTextContent(/Could not check/)
}, 25000)

it('keeps the instruction when the refusal follows a real outage', async () => {
  // The mirror of the case above, and the one the branch order gets wrong if
  // it is unconditional. Here the stale value is *false* and the page is still
  // acting on it -- Connect stays disabled -- so withdrawing the sentence
  // leaves a greyed-out button with no stated reason and tells a student to
  // wait for a check instead of starting the service that is actually down.
  //
  // Which is why `available` starts at null rather than false: without a third
  // value, this state and "nobody has checked yet" are the same one, and the
  // branch has to treat them alike.
  eegHealth.mockResolvedValueOnce({ available: false, error: 'Failed to fetch' })
  eegHealth.mockResolvedValue({ refused: true, error: 'Too many requests.' })
  render(<Adaptive />)

  expect(await screen.findByText(/EEG service/)).toHaveTextContent(/not reachable on port 8001/)
  await screen.findByText('status unavailable', undefined, { timeout: 8000 })

  const sentence = screen.getByText(/EEG service/)
  // Three things, and the first is the one this test is named for: the step
  // that fixes it has to survive the refusal. Asserting only that the sentence
  // still mentions an outage passes against a version that drops the
  // instruction and leaves a student with a disabled button and no next move.
  expect(sentence).toHaveTextContent(/Make sure the EEGResearch backend is running/)
  expect(sentence).toHaveTextContent(/not reachable/)
  expect(sentence).toHaveTextContent(/could not re-check/)
  // And not the sentence for an unknown state -- it would deny the very thing
  // the page is still acting on.
  expect(sentence).not.toHaveTextContent(/says nothing about your headband/)
}, 25000)


it('still reports a sidecar that genuinely did not answer', async () => {
  // The other half: this must not have turned every failure into "unknown".
  // A probe that ran and found nothing there has earned both the badge and the
  // sentence, port and all -- that one really is an unreachable backend.
  eegHealth.mockResolvedValue({ available: false, error: 'Failed to fetch' })
  render(<Adaptive />)

  expect(await screen.findByText('offline')).toBeInTheDocument()
  expect(screen.queryByText('status unavailable')).not.toBeInTheDocument()
  expect(screen.getByText(/EEG service/)).toHaveTextContent(/not reachable on port 8001/)
})


/**
 * The status poll is the other writer of `available`, and it needs a session
 * to mount -- which is why none of the tests above can see this interaction at
 * all: they render the page and never start one.
 */
const startASession = async () => {
  overrideApi(p => p.startsWith('/api/generate-question'), () => ({
    id: 'q1', question_text: 'What is 2 + 2?', question_topic: 'ordering',
    answer_options: ['3', '4', '5'], correct_answer: '4', difficulty: 'easy',
  }))
  await userEvent.click(await screen.findByRole('button', { name: /generate question/i }))
  await screen.findByText('What is 2 + 2?')
}

it('lets an answered status tick clear a refusal the health probe could not', async () => {
  // `/api/eeg/status` resolves a caller, so it is not in the public limiter and
  // the address budget cannot refuse it. Mid-lesson it therefore learns the
  // same fact `/api/eeg/health` was refused for -- and until it cleared the
  // flag, the page said it could not check the service while holding a
  // successful check of exactly that, seconds old, and withheld "ready" from a
  // sidecar it had confirmed.
  eegHealth.mockResolvedValue({ refused: true, error: 'Too many requests.' })
  eegStatus.mockResolvedValue({ ingest_mode: 'pull', service: true, poller: {} })
  render(<Adaptive />)

  // Before a session exists that poll is not mounted, so the refusal stands --
  // the state every test above lives in.
  await screen.findByText('status unavailable')

  await startASession()

  expect(await screen.findByText('ready')).toBeInTheDocument()
  expect(screen.queryByText('status unavailable')).not.toBeInTheDocument()
  expect(screen.getByText(/EEG service/)).toHaveTextContent(/EEG service ready/)
})

it('leaves the refusal standing when the status tick did not answer either', async () => {
  // `eegStatus` swallows its own failure into `service: false`, so clearing the
  // flag on that would turn "we could not check" into "we checked and it is
  // down" -- and the sentence turns that into an instruction to go and restart
  // something, on the strength of a request that never landed.
  eegHealth.mockResolvedValue({ refused: true, error: 'Too many requests.' })
  eegStatus.mockResolvedValue({ answered: false, service: false, poller: { running: false } })
  render(<Adaptive />)
  await startASession()

  expect(await screen.findByText('status unavailable')).toBeInTheDocument()
  expect(screen.queryByText('offline')).not.toBeInTheDocument()
  const sentence = screen.getByText(/EEG service/)
  expect(sentence).toHaveTextContent(/Could not check/)
  expect(sentence).not.toHaveTextContent(/Make sure/)
})


it('does not flip a push deployment to pull because a tick did not land', async () => {
  // `ingest_mode` read from an unlanded response is undefined, so push became
  // pull and the whole panel changed branch -- ending at "not reachable on
  // port 8001", which names a port and a service a push deployment does not
  // have. `eeg_health` answers `available: None` under push precisely to keep
  // that sentence off the first screen a student sees.
  //
  // A deployment's ingest mode cannot change because a request failed, which
  // is a stronger claim than the one about `available`: there is no reading to
  // go stale, only a fact about how this installation is wired.
  eegHealth.mockResolvedValue({ available: null, ingest_mode: 'push' })
  eegStatus.mockResolvedValue({ answered: false, service: false, poller: { running: false } })
  render(<Adaptive />)

  expect(await screen.findByText('on your device')).toBeInTheDocument()

  await startASession()
  await waitFor(() => expect(eegStatus).toHaveBeenCalled())

  expect(screen.getByText('on your device')).toBeInTheDocument()
  expect(screen.queryByText('offline')).not.toBeInTheDocument()
  // The push branch's own sentence, which can only render while `pushMode`
  // holds. Asserting the *absence* of "not reachable on port 8001" instead
  // passes for the wrong reason: the debug readout carries that phrase too,
  // so the query matches a second element and says nothing about this panel.
  expect(screen.getByText(/pairs through the app on this computer/)).toBeInTheDocument()
  expect(screen.queryByText(/EEG service not reachable/)).not.toBeInTheDocument()
})


// A link the bridge already holds, so Connect adopts it instead of walking the
// scan/connect chain -- the cheap way into a streaming push session.
// `eeg_age_ms` is part of "connected": the page counts a link as alive only
// with EEG flowing on it.
const ALREADY_CONNECTED = {
  muse_connected: true, muse_devices: ['Muse-1'], battery_percent: 80,
  auto_reconnect: true, reconnecting: false, reconnect_attempt: 0,
  reconnect_max_attempts: 5, reconnect_exhausted: false, eeg_age_ms: 2,
}

it('does not tear a streaming push link down because a tick did not land', async () => {
  // The two push exemptions below the guard read `s.ingest_mode` straight off
  // the response, so on an unlanded tick they lifted: `connected` was written
  // from a poller that does not exist under push, and `battery` from a muse
  // block that was never in the payload. Both belong to the telemetry poll
  // under push -- this one is not their writer at all, which is exactly what
  // the exemptions say and what an undefined field defeated.
  eegHealth.mockResolvedValue({ available: null, ingest_mode: 'push' })
  eegStatus.mockResolvedValue({ ingest_mode: 'push', service: null, poller: {} })
  devices.mockResolvedValue([{ device_id: 'default', kind: 'muse', running: true }])
  museState.mockResolvedValue({ running: true, ingestion: ALREADY_CONNECTED })
  render(<Adaptive />)

  await startASession()
  const button = await screen.findByRole('button', { name: /connect headband/i })
  await waitFor(() => expect(button).not.toBeDisabled())
  fireEvent.click(button)
  await screen.findByText(/STREAMING/, {}, { timeout: 10000 })
  // The charge arrives on the telemetry poll's own cycle, which is not the one
  // that painted STREAMING -- so this waits rather than reading straight after.
  await screen.findByLabelText(/Headband charge 80%/, {}, { timeout: 8000 })

  const before = eegStatus.mock.calls.length
  eegStatus.mockResolvedValue({ answered: false, service: false, poller: { running: false } })
  await waitFor(() => expect(eegStatus.mock.calls.length).toBeGreaterThan(before),
                { timeout: 8000 })

  // Still streaming, and still holding the reading it had. A student mid-
  // lesson would otherwise watch the panel drop to "Connect Headband" because
  // one request to an unrelated endpoint did not land.
  expect(screen.getByText(/STREAMING/)).toBeInTheDocument()
  expect(screen.getByLabelText(/Headband charge 80%/)).toBeInTheDocument()
}, 30000)


it('writes nothing at all from a tick that did not land, under pull too', async () => {
  // Under pull no exemption stands between the swallowed fallback and the
  // panel: `connected` came from a poller that is server-side and entirely
  // unaffected by one browser request failing, `battery` from an absent muse
  // block, and `samples` -- which under pull *is* what the sub-line renders --
  // from an absent poller block. One failed tick took a streaming session to
  // "Connect Headband" over a sentence saying the teacher can see it live,
  // with no toast, because `phase` stayed `connected` while `connected` went
  // false. Clicking that button runs disconnect->scan->connect and drops a
  // link that was working.
  const streaming = {
    ingest_mode: 'pull', service: true,
    poller: { running: true, samples: 3, last_ts: '2026-09-20T00:00:00Z' },
    muse: { ingestion: { battery_percent: 80 } },
  }
  eegStatus.mockResolvedValue(streaming)
  // The bridge agrees, so the telemetry poll beside this one is not the thing
  // moving the charge.
  museState.mockResolvedValue({
    running: true, ingestion: { muse_connected: true, battery_percent: 80, eeg_age_ms: 2 },
  })
  render(<Adaptive />)
  await startASession()

  await screen.findByText(/STREAMING/, {}, { timeout: 8000 })
  await screen.findByLabelText(/Headband charge 80%/, {}, { timeout: 8000 })
  expect(screen.getByText(/3 samples sent/)).toBeInTheDocument()

  const before = eegStatus.mock.calls.length
  eegStatus.mockResolvedValue({ answered: false, service: false, poller: { running: false } })
  await waitFor(() => expect(eegStatus.mock.calls.length).toBeGreaterThan(before),
                { timeout: 8000 })

  expect(screen.getByText(/STREAMING/)).toBeInTheDocument()
  expect(screen.getByLabelText(/Headband charge 80%/)).toBeInTheDocument()
  expect(screen.getByText(/3 samples sent/)).toBeInTheDocument()
  // The button is the sharp end: it is what a student would act on.
  expect(screen.getByRole('button', { name: /disconnect/i })).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: /connect headband/i })).not.toBeInTheDocument()
}, 30000)
