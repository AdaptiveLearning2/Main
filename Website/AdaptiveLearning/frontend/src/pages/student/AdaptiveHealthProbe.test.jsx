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
  // **`clearAllMocks` clears calls, never implementations.** A
  // `mockResolvedValue` set by one test is still in force for every test
  // declared after it, so the push-mode health answer below leaks forward and
  // a later pull test silently runs against `ingest_mode: 'push'` -- which
  // re-points the samples line at `push.recorded` and reads 0. Restate the
  // factory defaults here so each test starts from the declared one.
  eegHealth.mockResolvedValue({ available: true, ingest_mode: 'pull' })
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
  // `answered: false`: this fixture means the request never landed. Without
  // the marker it is the backend's *auth-error* answer -- reachable sidecar,
  // misconfigured token -- which now renders differently and should.
  // An *answered* outage: the probe reached the backend and it reported the
  // sidecar down. A non-answer here would not be a known outage at all.
  eegHealth.mockResolvedValueOnce({ available: false, ingest_mode: 'pull', url: 'http://localhost:8001' })
  eegHealth.mockResolvedValue({ refused: true, error: 'Too many requests.' })
  render(<Adaptive />)

  // Waits for *this* sentence, not for any node mentioning the service. The
  // panel shows "Checking the EEG service…" until the first probe answers --
  // `available` is null until then -- and `findByText(/EEG service/)` matches
  // that immediately, so the assertion ran against the unchecked state. It
  // passed locally, where the probe resolves before the query, and failed in
  // CI. Anchoring on the text the settled state produces is what makes the
  // wait mean what it says.
  await screen.findByText(/not reachable on port 8001/)
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
  eegHealth.mockResolvedValue({ available: false, ingest_mode: 'pull', url: 'http://localhost:8001' })
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

it('keeps a push deployment in push mode when the health probe stops answering', async () => {
  // `eegHealth` swallows a non-429 failure into `{available: false}`, which
  // carries no `ingest_mode` -- so `h.ingest_mode === 'push'` reads false and
  // the page decides this deployment is pull because one browser request
  // failed. Under push that lifts the exemptions below, and re-points the
  // samples line at `push.recorded` while the poller's own count is what is on
  // screen. `available` may move on a failed probe; the mode may not.
  //
  // The badge is the discriminator, as it is for the test above it: it renders
  // if and only if `pushMode`, and unlike the samples count it does not read 0
  // in both modes, which is what made every other observable here useless.
  // No session and no pairing -- the health effect runs from mount, so this
  // isolates the one write under test.
  eegHealth.mockResolvedValue({ available: null, ingest_mode: 'push' })
  render(<Adaptive />)
  expect(await screen.findByText('on your device')).toBeInTheDocument()

  const before = eegHealth.mock.calls.length
  // `answered: false` because that is what `eegHealth` returns on a non-429
  // failure -- a marker it carries so a client-side failure is not
  // shape-identical to the backend's deliberate `{available: false}`, which it
  // sends when the sidecar is reachable and the learner token is
  // misconfigured. The guard under test reads `ingest_mode`, which neither
  // carries, so this fixture is about matching the real fallback rather than
  // about what makes the test pass -- a mock that drifts from the function it
  // stands in for is the next bug's hiding place.
  eegHealth.mockResolvedValue({ answered: false, available: false, error: 'Failed to fetch' })
  // Two further probes, so a wrong write has landed and painted rather than
  // this asserting into the gap before the first one.
  await waitFor(() => expect(eegHealth.mock.calls.length).toBeGreaterThan(before + 1),
                { timeout: 20000 })

  expect(screen.getByText('on your device')).toBeInTheDocument()
}, 30000)

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
  // **Three calls, not one.** Both polls call `eegStatus` with the same
  // argument under pull, so the counter cannot tell them apart and advancing
  // it by one proves only that the 3 s status tick ran -- which happens before
  // the 5 s telemetry poll re-reads, so a charge that poll clears is still on
  // screen when a one-call wait returns. Three spans a telemetry cycle. It is
  // cadence-derived rather than a recorded event because there is no event to
  // record: one function, one argument, two callers.
  await waitFor(() => expect(eegStatus.mock.calls.length).toBeGreaterThan(before + 2),
                { timeout: 20000 })

  expect(screen.getByText(/STREAMING/)).toBeInTheDocument()
  expect(screen.getByLabelText(/Headband charge 80%/)).toBeInTheDocument()
  expect(screen.getByText(/3 samples sent/)).toBeInTheDocument()
  // The button is the sharp end: it is what a student would act on.
  expect(screen.getByRole('button', { name: /disconnect/i })).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: /connect headband/i })).not.toBeInTheDocument()
}, 30000)


it('tells a reachable-but-misconfigured service apart from an unreachable one', async () => {
  // `/api/eeg/health` answers `{available: false, error}` when the sidecar is
  // reachable and the learner token is wrong -- "a config error, not an
  // outage, so report it rather than 500". `eegHealth`'s own catch produced
  // the same shape, so both rendered as "not reachable on port 8001. Make
  // sure the EEGResearch backend is running": the one instruction that cannot
  // help, naming the one layer that is demonstrably fine.
  eegHealth.mockResolvedValue({
    available: false, url: 'http://localhost:8001',
    error: 'EEG_API_TOKEN is not set',
  })
  render(<Adaptive />)

  // Precise, not `/EEG/`: the debug readout carries that string too, and a
  // findBy matching twice retries until the budget, surfacing as a timeout
  // rather than as the ambiguity it is.
  const sentence = await screen.findByText(/is running but is not set up/)
  expect(sentence).not.toHaveTextContent(/Make sure the EEGResearch backend is running/)
  // And the badge follows it: a service that answered is not offline.
  expect(screen.getByText('needs setup')).toBeInTheDocument()
  expect(screen.queryByText('offline')).not.toBeInTheDocument()
  // The detail is a server-side configuration string, not something a student
  // can act on, so it stays out of the copy.
  expect(document.body.textContent).not.toMatch(/EEG_API_TOKEN/)
})

it('lets a healthy pull answer set the mode, which omits ingest_mode', async () => {
  // The guard this replaces read a missing `ingest_mode` as "did not land" --
  // but the backend omits that field on two of its four shapes, including the
  // ordinary healthy pull answer. So the probe could no longer set
  // `pushMode: false` at all, and a push-to-pull reconfiguration stopped
  // self-correcting mid-session. Harmless only because `undefined` and `false`
  // are both falsy and `undefined` is the initial value.
  eegHealth.mockResolvedValueOnce({ available: null, ingest_mode: 'push', url: null })
  eegHealth.mockResolvedValue({ available: true, url: 'http://localhost:8001', muse: {} })
  render(<Adaptive />)

  await screen.findByText('on your device')
  // The pull answer carries no `ingest_mode`, and has to be believed anyway.
  expect(await screen.findByText('ready', undefined, { timeout: 8000 })).toBeInTheDocument()
  expect(screen.queryByText('on your device')).not.toBeInTheDocument()
}, 20000)


it('does not let a probe that reached nothing erase a fault the backend stated', async () => {
  // The config error is a fact the backend *reported*: the sidecar is
  // reachable and the token is wrong. A later request that reached nothing
  // changes none of that, so clearing the field there swapped a known fault
  // for "Make sure the EEGResearch backend is running" -- the one instruction
  // the erased sentence says cannot help.
  eegHealth.mockResolvedValueOnce({
    available: false, url: 'http://localhost:8001', error: 'EEG_API_TOKEN is not set',
  })
  eegHealth.mockResolvedValue({ answered: false, available: false, error: 'Failed to fetch' })
  render(<Adaptive />)

  await screen.findByText(/is running but is not set up/)

  const before = eegHealth.mock.calls.length
  await waitFor(() => expect(eegHealth.mock.calls.length).toBeGreaterThan(before),
                { timeout: 8000 })

  // Still on file -- which is what this test is for -- and now stated as the
  // stale fact it is, rather than as the current state of a service nothing
  // has reached since.
  expect(screen.getByText(/when we last checked/)).toHaveTextContent(/not set up to use the headband/)
  expect(screen.queryByText(/Make sure the EEGResearch backend is running/)).not.toBeInTheDocument()
  expect(screen.getByText('needs setup · unchecked')).toBeInTheDocument()
  expect(screen.queryByText('offline')).not.toBeInTheDocument()
}, 20000)

it('keeps the known fault ahead of the unread state when the probe is refused', async () => {
  // The 429 path early-returns before the writer, so `serviceError` correctly
  // survives -- and the sentence chain ignored it, preferring an outage branch
  // whose advice the config sentence explicitly contradicts. Most specific
  // known fact first, the unread state after it, as `cellLabel` orders the
  // cohort roster.
  eegHealth.mockResolvedValueOnce({
    available: false, url: 'http://localhost:8001', error: 'EEG_API_TOKEN is not set',
  })
  eegHealth.mockResolvedValue({ refused: true, error: 'Too many requests.' })
  render(<Adaptive />)

  await screen.findByText(/is running but is not set up/)
  await screen.findByText(/could not re-check just now/, undefined, { timeout: 8000 })

  // Both halves in one sentence: what is known, and that it could not be
  // confirmed -- not one replacing the other.
  const sentence = screen.getByText(/could not re-check just now/)
  expect(sentence).toHaveTextContent(/not set up to use the headband/)
  expect(sentence).toHaveTextContent(/Restarting it will not help/)
  expect(sentence).not.toHaveTextContent(/Make sure the EEGResearch backend is running/)
  // And the badge follows it rather than the refusal.
  // The chip carries the qualifier as well: two words asserting a current
  // state from evidence of unknown age is the same claim in miniature.
  expect(screen.getByText('needs setup · unchecked')).toBeInTheDocument()
  expect(screen.queryByText('status unavailable')).not.toBeInTheDocument()
}, 20000)


it('does not end a refusal with a request that established nothing', async () => {
  // `probeRefused: false` sat outside the guard, undefended by the comment
  // above it, which justified only `available`. Cleared from a non-answer,
  // "could not check" became a confident outage claim on the strength of a
  // request that checked nothing at all.
  eegHealth.mockResolvedValueOnce({ refused: true, error: 'Too many requests.' })
  eegHealth.mockResolvedValue({ answered: false, available: false, error: 'Failed to fetch' })
  render(<Adaptive />)

  await screen.findByText(/Could not check the EEG service/)

  const before = eegHealth.mock.calls.length
  await waitFor(() => expect(eegHealth.mock.calls.length).toBeGreaterThan(before),
                { timeout: 8000 })

  expect(screen.getByText(/Could not check the EEG service/)).toBeInTheDocument()
  expect(screen.queryByText(/Make sure the EEGResearch backend is running/)).not.toBeInTheDocument()
  expect(screen.getByText('status unavailable')).toBeInTheDocument()
  expect(screen.queryByText('offline')).not.toBeInTheDocument()
}, 20000)

it('names the server it could not reach, not the one it never probed', async () => {
  // Nothing has been checked: the request went to this app's backend and did
  // not arrive, so the sidecar on 8001 was never asked. The old sentence named
  // that service and that port anyway -- the commonest failure of the set,
  // wearing the message for a different one.
  eegHealth.mockResolvedValue({ answered: false, available: false, error: 'Failed to fetch' })
  render(<Adaptive />)

  const sentence = await screen.findByText(/hasn't been checked/)
  expect(sentence).not.toHaveTextContent(/8001/)
  expect(sentence).not.toHaveTextContent(/EEGResearch/)
  // Not offline either: "offline" is an answered claim about the sidecar.
  expect(screen.getByText('status unavailable')).toBeInTheDocument()
  expect(screen.queryByText('offline')).not.toBeInTheDocument()
})

it('still names the sidecar when a probe answered that it is down', async () => {
  // The branch the sentence above must not swallow: this one was answered, so
  // the port and the instruction are the right ones.
  eegHealth.mockResolvedValue({ available: false, ingest_mode: 'pull', url: 'http://localhost:8001' })
  render(<Adaptive />)

  expect(await screen.findByText(/EEG service not reachable on port 8001/)).toBeInTheDocument()
  expect(await screen.findByText('offline')).toBeInTheDocument()
  expect(screen.queryByText('status unavailable')).not.toBeInTheDocument()
})


it('qualifies a known fault the unreachable server has not re-confirmed', async () => {
  // The decisive pair. A refusal already turned the claim into "when we last
  // checked"; a probe that reached nothing is the same kind of staleness and
  // said nothing at all -- the sentence was byte-identical to the one shown
  // while the probe was answering. And it withheld the more urgent fact, which
  // nothing else on screen carried: it is this app's own backend that cannot
  // be reached.
  eegHealth.mockResolvedValueOnce({
    available: false, url: 'http://localhost:8001', error: 'EEG_API_TOKEN is not set',
  })
  eegHealth.mockResolvedValue({ answered: false, available: false, error: 'Failed to fetch' })
  render(<Adaptive />)

  const fresh = await screen.findByText(/is running but is not set up/)
  expect(fresh).toBeInTheDocument()

  const stale = await screen.findByText(/when we last checked/, undefined, { timeout: 8000 })
  expect(stale).toHaveTextContent(/server can't be reached right now/)
  expect(stale).toHaveTextContent(/Restarting it will not help/)
  // Not the refusal's qualifier: that one says the check was declined, which
  // is a different reason and a different thing to wait for.
  expect(stale).not.toHaveTextContent(/could not re-check just now/)
  expect(screen.getByText('needs setup · unchecked')).toBeInTheDocument()
}, 20000)


it('does not report an outage from a probe that has not come back', async () => {
  // `available` starts at null so that "nobody has checked" and "checked, and
  // it is down" stay apart -- and the terminal branch tested it for falsiness,
  // so the two were one state there. Rendered with the very first probe still
  // in flight, the page named a service and a port it had not contacted and
  // told the reader to go and start it.
  //
  // Nothing bounds how long that lasts. `apiFetch`'s `timeoutMs` has no
  // default and `eegHealth` passes no options, so against a backend that never
  // answers the request never settles, `probeUnreachable` never becomes true,
  // and the false claim stands for the whole lesson.
  eegHealth.mockImplementation(() => new Promise(() => {}))
  render(<Adaptive />)

  const sentence = await screen.findByText(/Checking the EEG service/)
  // Scoped to the status line: the debug readout carries a sentence of its own
  // naming the same port, from its own state, so a document-wide query here
  // would fail for a reason this test is not about.
  expect(sentence).not.toHaveTextContent(/not reachable on port 8001/)
  // And the badge: `offline` is an answered claim about the sidecar, and no
  // answer has arrived.
  expect(screen.queryByText('offline')).not.toBeInTheDocument()
})
