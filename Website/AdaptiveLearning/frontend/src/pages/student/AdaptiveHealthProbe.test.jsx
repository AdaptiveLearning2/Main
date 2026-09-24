/** A refused health probe is not an offline headband: reachable, not reachable, and unanswered are three states. */
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
// Every export the page imports: the push test reaches all of them.
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
  // clearAllMocks keeps implementations, so restate the default or a push answer leaks forward.
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
  // Refused from the first probe, so `available` is null, not false.
  eegHealth.mockResolvedValue({ refused: true, error: 'Too many requests. Slow down.' })
  render(<Adaptive />)

  expect(await screen.findByText('status unavailable')).toBeInTheDocument()
  expect(screen.queryByText('offline')).not.toBeInTheDocument()

  // The sentence is a second copy of the badge's claim and needs its own query.
  const sentence = await screen.findByText(/EEG service/)
  expect(sentence).toHaveTextContent(/Could not check/)
  expect(sentence).not.toHaveTextContent(/port 8001/)
  expect(sentence).not.toHaveTextContent(/backend is running/)
})

it('goes on acting on the last answer without claiming it is current', async () => {
  // Discovery and Connect are gated on `available`, so it stays stale; the badge must still not say "ready".
  eegHealth.mockResolvedValueOnce({ available: true, ingest_mode: 'pull' })
  eegHealth.mockResolvedValue({ refused: true, error: 'Too many requests.' })
  render(<Adaptive />)

  await screen.findByText('ready')
  await screen.findByText('status unavailable', undefined, { timeout: 8000 })

  // Discovery tears down when `available` goes false, so continued calls prove it did not.
  const during = eegDevices.mock.calls.length
  await waitFor(() => expect(eegDevices.mock.calls.length).toBeGreaterThan(during),
                { timeout: 8000 })

  // Exactly one of the three badges.
  expect(screen.getByText('status unavailable')).toBeInTheDocument()
  expect(screen.queryByText('ready')).not.toBeInTheDocument()
  expect(screen.queryByText('offline')).not.toBeInTheDocument()
  expect(screen.getByText(/EEG service/)).toHaveTextContent(/Could not check/)
}, 25000)

it('keeps the instruction when the refusal follows a real outage', async () => {
  // Stale value is false and Connect stays disabled, so the sentence must keep saying why.
  eegHealth.mockResolvedValueOnce({ available: false, ingest_mode: 'pull', url: 'http://localhost:8001' })
  eegHealth.mockResolvedValue({ refused: true, error: 'Too many requests.' })
  render(<Adaptive />)

  // Anchor on the settled text: /EEG service/ also matches the initial "Checking…" line.
  await screen.findByText(/not reachable on port 8001/)
  await screen.findByText('status unavailable', undefined, { timeout: 8000 })

  const sentence = screen.getByText(/EEG service/)
  expect(sentence).toHaveTextContent(/Make sure the EEGResearch backend is running/)
  expect(sentence).toHaveTextContent(/not reachable/)
  expect(sentence).toHaveTextContent(/could not re-check/)
  expect(sentence).not.toHaveTextContent(/says nothing about your headband/)
}, 25000)


it('still reports a sidecar that genuinely did not answer', async () => {
  // An answered "down" has earned both the badge and the port.
  eegHealth.mockResolvedValue({ available: false, ingest_mode: 'pull', url: 'http://localhost:8001' })
  render(<Adaptive />)

  expect(await screen.findByText('offline')).toBeInTheDocument()
  expect(screen.queryByText('status unavailable')).not.toBeInTheDocument()
  expect(screen.getByText(/EEG service/)).toHaveTextContent(/not reachable on port 8001/)
})


/** The status poll, the other writer of `available`, mounts only with a session. */
const startASession = async () => {
  overrideApi(p => p.startsWith('/api/generate-question'), () => ({
    id: 'q1', question_text: 'What is 2 + 2?', question_topic: 'ordering',
    answer_options: ['3', '4', '5'], correct_answer: '4', difficulty: 'easy',
  }))
  await userEvent.click(await screen.findByRole('button', { name: /generate question/i }))
  await screen.findByText('What is 2 + 2?')
}

it('lets an answered status tick clear a refusal the health probe could not', async () => {
  // `/api/eeg/status` resolves a caller, so the address budget cannot refuse it.
  eegHealth.mockResolvedValue({ refused: true, error: 'Too many requests.' })
  eegStatus.mockResolvedValue({ ingest_mode: 'pull', service: true, poller: {} })
  render(<Adaptive />)

  await screen.findByText('status unavailable')

  await startASession()

  expect(await screen.findByText('ready')).toBeInTheDocument()
  expect(screen.queryByText('status unavailable')).not.toBeInTheDocument()
  expect(screen.getByText(/EEG service/)).toHaveTextContent(/EEG service ready/)
})

it('leaves the refusal standing when the status tick did not answer either', async () => {
  // `eegStatus` swallows failure into `service: false`; that must not read as "checked and down".
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
  // Ingest mode is a fact about the installation; a failed request cannot change it.
  eegHealth.mockResolvedValue({ available: null, ingest_mode: 'push' })
  eegStatus.mockResolvedValue({ answered: false, service: false, poller: { running: false } })
  render(<Adaptive />)

  expect(await screen.findByText('on your device')).toBeInTheDocument()

  await startASession()
  await waitFor(() => expect(eegStatus).toHaveBeenCalled())

  expect(screen.getByText('on your device')).toBeInTheDocument()
  expect(screen.queryByText('offline')).not.toBeInTheDocument()
  // Assert the push sentence's presence: the debug readout also carries "not reachable on port 8001".
  expect(screen.getByText(/pairs through the app on this computer/)).toBeInTheDocument()
  expect(screen.queryByText(/EEG service not reachable/)).not.toBeInTheDocument()
})


// A link the bridge already holds, so Connect adopts it; `eeg_age_ms` is required for "connected".
const ALREADY_CONNECTED = {
  muse_connected: true, muse_devices: ['Muse-1'], battery_percent: 80,
  auto_reconnect: true, reconnecting: false, reconnect_attempt: 0,
  reconnect_max_attempts: 5, reconnect_exhausted: false, eeg_age_ms: 2,
}

it('keeps a push deployment in push mode when the health probe stops answering', async () => {
  // A failed probe has no `ingest_mode`; the badge renders iff `pushMode`.
  eegHealth.mockResolvedValue({ available: null, ingest_mode: 'push' })
  render(<Adaptive />)
  expect(await screen.findByText('on your device')).toBeInTheDocument()

  const before = eegHealth.mock.calls.length
  // Matches `eegHealth`'s real non-429 fallback shape.
  eegHealth.mockResolvedValue({ answered: false, available: false, error: 'Failed to fetch' })
  // Two further probes, so a wrong write has landed and painted.
  await waitFor(() => expect(eegHealth.mock.calls.length).toBeGreaterThan(before + 1),
                { timeout: 20000 })

  expect(screen.getByText('on your device')).toBeInTheDocument()
}, 30000)

it('does not tear a streaming push link down because a tick did not land', async () => {
  // Under push the telemetry poll, not this one, writes `connected` and `battery`.
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
  // The charge arrives on the telemetry poll's own cycle.
  await screen.findByLabelText(/Headband charge 80%/, {}, { timeout: 8000 })

  const before = eegStatus.mock.calls.length
  eegStatus.mockResolvedValue({ answered: false, service: false, poller: { running: false } })
  await waitFor(() => expect(eegStatus.mock.calls.length).toBeGreaterThan(before),
                { timeout: 8000 })

  expect(screen.getByText(/STREAMING/)).toBeInTheDocument()
  expect(screen.getByLabelText(/Headband charge 80%/)).toBeInTheDocument()
}, 30000)


it('writes nothing at all from a tick that did not land, under pull too', async () => {
  // Under pull nothing shields `connected`, `battery` or `samples` from the swallowed fallback.
  const streaming = {
    ingest_mode: 'pull', service: true,
    poller: { running: true, samples: 3, last_ts: '2026-09-20T00:00:00Z' },
    muse: { ingestion: { battery_percent: 80 } },
  }
  eegStatus.mockResolvedValue(streaming)
  // The bridge agrees, so the telemetry poll is not what moves the charge.
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
  // Both polls call `eegStatus` identically under pull; three calls spans a 5 s telemetry cycle.
  await waitFor(() => expect(eegStatus.mock.calls.length).toBeGreaterThan(before + 2),
                { timeout: 20000 })

  expect(screen.getByText(/STREAMING/)).toBeInTheDocument()
  expect(screen.getByLabelText(/Headband charge 80%/)).toBeInTheDocument()
  expect(screen.getByText(/3 samples sent/)).toBeInTheDocument()
  expect(screen.getByRole('button', { name: /disconnect/i })).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: /connect headband/i })).not.toBeInTheDocument()
}, 30000)


it('tells a reachable-but-misconfigured service apart from an unreachable one', async () => {
  // Backend's answer when the sidecar is reachable but the learner token is wrong.
  eegHealth.mockResolvedValue({
    available: false, url: 'http://localhost:8001',
    error: 'EEG_API_TOKEN is not set',
  })
  render(<Adaptive />)

  // Precise, not /EEG/: the debug readout matches too, and a double match times out.
  const sentence = await screen.findByText(/is running but is not set up/)
  expect(sentence).not.toHaveTextContent(/Make sure the EEGResearch backend is running/)
  expect(screen.getByText('needs setup')).toBeInTheDocument()
  expect(screen.queryByText('offline')).not.toBeInTheDocument()
  // Server-side config detail stays out of student copy.
  expect(document.body.textContent).not.toMatch(/EEG_API_TOKEN/)
})

it('lets a healthy pull answer set the mode, which omits ingest_mode', async () => {
  // The healthy pull answer omits `ingest_mode` and must still set `pushMode: false`.
  eegHealth.mockResolvedValueOnce({ available: null, ingest_mode: 'push', url: null })
  eegHealth.mockResolvedValue({ available: true, url: 'http://localhost:8001', muse: {} })
  render(<Adaptive />)

  await screen.findByText('on your device')
  expect(await screen.findByText('ready', undefined, { timeout: 8000 })).toBeInTheDocument()
  expect(screen.queryByText('on your device')).not.toBeInTheDocument()
}, 20000)


it('does not let a probe that reached nothing erase a fault the backend stated', async () => {
  // A config error the backend reported survives a later probe that reached nothing.
  eegHealth.mockResolvedValueOnce({
    available: false, url: 'http://localhost:8001', error: 'EEG_API_TOKEN is not set',
  })
  eegHealth.mockResolvedValue({ answered: false, available: false, error: 'Failed to fetch' })
  render(<Adaptive />)

  await screen.findByText(/is running but is not set up/)

  const before = eegHealth.mock.calls.length
  await waitFor(() => expect(eegHealth.mock.calls.length).toBeGreaterThan(before),
                { timeout: 8000 })

  // Still on file, now stated as stale.
  expect(screen.getByText(/when we last checked/)).toHaveTextContent(/not set up to use the headband/)
  expect(screen.queryByText(/Make sure the EEGResearch backend is running/)).not.toBeInTheDocument()
  expect(screen.getByText('needs setup · unchecked')).toBeInTheDocument()
  expect(screen.queryByText('offline')).not.toBeInTheDocument()
}, 20000)

it('keeps the known fault ahead of the unread state when the probe is refused', async () => {
  // Most specific known fact first, then the unread state, as `cellLabel` orders the cohort roster.
  eegHealth.mockResolvedValueOnce({
    available: false, url: 'http://localhost:8001', error: 'EEG_API_TOKEN is not set',
  })
  eegHealth.mockResolvedValue({ refused: true, error: 'Too many requests.' })
  render(<Adaptive />)

  await screen.findByText(/is running but is not set up/)
  await screen.findByText(/could not re-check just now/, undefined, { timeout: 8000 })

  // Both halves in one sentence: what is known, and that it could not be confirmed.
  const sentence = screen.getByText(/could not re-check just now/)
  expect(sentence).toHaveTextContent(/not set up to use the headband/)
  expect(sentence).toHaveTextContent(/Restarting it will not help/)
  expect(sentence).not.toHaveTextContent(/Make sure the EEGResearch backend is running/)
  // The chip carries the qualifier too.
  expect(screen.getByText('needs setup · unchecked')).toBeInTheDocument()
  expect(screen.queryByText('status unavailable')).not.toBeInTheDocument()
}, 20000)


it('does not end a refusal with a request that established nothing', async () => {
  // A non-answer must not clear `probeRefused`.
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
  // The request never reached this app's backend, so the sidecar on 8001 was never asked.
  eegHealth.mockResolvedValue({ answered: false, available: false, error: 'Failed to fetch' })
  render(<Adaptive />)

  const sentence = await screen.findByText(/hasn't been checked/)
  expect(sentence).not.toHaveTextContent(/8001/)
  expect(sentence).not.toHaveTextContent(/EEGResearch/)
  expect(screen.getByText('status unavailable')).toBeInTheDocument()
  expect(screen.queryByText('offline')).not.toBeInTheDocument()
})

it('still names the sidecar when a probe answered that it is down', async () => {
  eegHealth.mockResolvedValue({ available: false, ingest_mode: 'pull', url: 'http://localhost:8001' })
  render(<Adaptive />)

  expect(await screen.findByText(/EEG service not reachable on port 8001/)).toBeInTheDocument()
  expect(await screen.findByText('offline')).toBeInTheDocument()
  expect(screen.queryByText('status unavailable')).not.toBeInTheDocument()
})


it('qualifies a known fault the unreachable server has not re-confirmed', async () => {
  // Unreachable backend is staleness like a refusal, and must also say our own server is down.
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
  // Not the refusal's qualifier: a different reason.
  expect(stale).not.toHaveTextContent(/could not re-check just now/)
  expect(screen.getByText('needs setup · unchecked')).toBeInTheDocument()
}, 20000)


it('does not report an outage from a probe that has not come back', async () => {
  // `available` is null until a probe answers, and nothing bounds how long that takes.
  eegHealth.mockImplementation(() => new Promise(() => {}))
  render(<Adaptive />)

  const sentence = await screen.findByText(/Checking the EEG service/)
  // Scoped to the status line: the debug readout names the same port.
  expect(sentence).not.toHaveTextContent(/not reachable on port 8001/)
  expect(screen.queryByText('offline')).not.toBeInTheDocument()
})
