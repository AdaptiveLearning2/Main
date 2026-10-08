/** "How many questions?" is a goal the page checks in at, never a cap that ends the session. */
import { it, expect, beforeEach, vi } from 'vitest'
import { render, screen, act } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

vi.mock('../../lib/api', async () => await import('../../test/mocks/apiFetch'))
vi.mock('../../lib/supabase', async () => await import('../../test/mocks/supabase'))
vi.mock('sonner', () => ({ toast: { error: vi.fn(), success: vi.fn() } }))
vi.mock('../../lib/session', () => ({
  endSession: vi.fn(async () => true),
  markEegStarted: vi.fn(async () => true),
  recordAnswer: vi.fn(async () => ({ topic: 'ordering' })),
}))
vi.mock('../../lib/signals', () => ({
  createSignalRecorder: () => ({ start: vi.fn(), stop: vi.fn() }),
  eegHealth: vi.fn(async () => ({ available: false })),
  eegStatus: vi.fn(async () => ({})),
  eegDevices: vi.fn(async () => ({ devices: [] })),
}))
vi.mock('../../lib/sidecar', () => ({
  startPush: vi.fn(), stopPush: vi.fn(), stopPushOnUnload: vi.fn(),
  pushStatus: vi.fn(async () => ({})), deviceStart: vi.fn(), museRefresh: vi.fn(),
  museConnect: vi.fn(), museDisconnect: vi.fn(), museStatus: vi.fn(async () => ({})),
}))
vi.mock('../../context/AuthContext', () => ({
  useAuth: () => ({ user: { id: 'u1', email: 'a@b.c' }, role: 'student', loading: false }),
}))

import { endSession, recordAnswer } from '../../lib/session'
import { mockApi, resetApi } from '../../test/mocks/apiFetch'
import { buildRecordingPermits } from '../../test/fixtures/recordingPermits'
import Adaptive from './Adaptive'

beforeEach(() => {
  resetApi()
  vi.clearAllMocks()
  mockApi({
    'GET /api/profile/me': () => ({ id: 'u1', role: 'student', grade_level: '1st Grade' }),
    'GET /api/recording/me': () => buildRecordingPermits(),
    'GET /api/performance/student/u1': () => [],
    'POST /api/sessions/start': () => ({ id: 'sess-goal' }),
    'GET /api/eeg/health': () => ({ available: false }),
  })
})

it('offers a choice of how many questions, and no limit', async () => {
  render(<Adaptive />)
  await screen.findByText(/how many questions/i)
  for (const label of ['5', '10', '15', '20', 'No limit']) {
    expect(screen.getByRole('button', { name: label })).toBeInTheDocument()
  }
})

it('says what picking one will do, without promising to stop', async () => {
  render(<Adaptive />)
  await screen.findByText(/how many questions/i)
  await userEvent.click(screen.getByRole('button', { name: '10' }))
  // "check in", not "stop after": the copy matches the behaviour.
  expect(await screen.findByText(/check in after/i)).toBeInTheDocument()
  expect(screen.getByText(/keep going/i)).toBeInTheDocument()
})

it('never ends the session just because a goal was picked', async () => {
  render(<Adaptive />)
  await screen.findByText(/how many questions/i)
  await userEvent.click(screen.getByRole('button', { name: '5' }))
  expect(endSession).not.toHaveBeenCalled()
})

// ── a goal silences the duration reminder for the sitting ────────────────────
// The sitting's explicit goal wins over the saved duration; "No limit" keeps the reminder.
// Real timers: the duration tick is 20s, so each of these costs ~22s.

const QUESTION = { question_text: 'What is 1 + 1?', answer_options: ['1', '2'], correct_answer: '2',
                   subject: 'expressions', difficulty: 'easy' }

// `mockApi` replaces the route table, so the happy path is restated here.
const ROUTES = {
  'GET /api/recording/me': () => buildRecordingPermits(),
  'GET /api/performance/student/u1': () => [],
  'POST /api/sessions/start': () => ({ id: 'sess-goal' }),
  'GET /api/eeg/health': () => ({ available: false }),
}

async function startWithDuration(goal) {
  mockApi({
    ...ROUTES,
    'GET /api/profile/me': () => ({ id: 'u1', role: 'student', grade_level: '1st Grade',
                                    session_duration_minutes: 0.001 }),
    'GET /api/generate-question?bias=0&grade=1st+Grade&session_id=sess-goal': () => QUESTION,
  })
  render(<Adaptive />)
  await screen.findByText(/how many questions/i)
  if (goal) await userEvent.click(screen.getByRole('button', { name: goal }))
  await userEvent.click(screen.getByRole('button', { name: /generate question/i }))
  await screen.findByText(/What is 1 \+ 1\?/)
  await new Promise(r => setTimeout(r, 22000))
}

it('reminds about the duration when no goal was picked', async () => {
  await startWithDuration(null)
  expect(screen.getByText(/That is your 0\.001 minutes/)).toBeInTheDocument()
}, 40_000)

it('does not remind about the duration once a goal is picked', async () => {
  await startWithDuration('5')
  expect(screen.queryByText(/That is your 0\.001 minutes/)).toBeNull()
}, 40_000)

it('says which reminder is in force', async () => {
  mockApi({
    ...ROUTES,
    'GET /api/profile/me': () => ({ id: 'u1', role: 'student', grade_level: '1st Grade',
                                    session_duration_minutes: 15 }),
  })
  render(<Adaptive />)
  await screen.findByText(/how many questions/i)
  expect(await screen.findByText(/check in after/i)).toHaveTextContent(/15 minutes/)
  await userEvent.click(screen.getByRole('button', { name: '10' }))
  expect(screen.getByText(/check in after/i)).toHaveTextContent(/after 10 instead of after 15 minutes/)
  await userEvent.click(screen.getByRole('button', { name: 'No limit' }))
  expect(screen.getByText(/check in after/i)).toHaveTextContent(/15 minutes/)
})

it('counts an answer only once it has been recorded', async () => {
  mockApi({
    ...ROUTES,
    'GET /api/profile/me': () => ({ id: 'u1', role: 'student', grade_level: '1st Grade' }),
    'GET /api/generate-question?bias=0&grade=1st+Grade&session_id=sess-goal': () => QUESTION,
  })
  const answer = async () => {
    await userEvent.click(await screen.findByRole('button', { name: /generate question|next question/i }))
    await userEvent.click(await screen.findByRole('button', { name: /^B\s*2$/ }))
    await userEvent.click(screen.getByRole('button', { name: /submit answer/i }))
  }
  let settle
  recordAnswer.mockImplementationOnce(() => new Promise(r => { settle = r }))
  render(<Adaptive />)
  await answer()
  // Sent, not yet stored: no count.
  await screen.findByRole('button', { name: /next question/i })
  expect(screen.queryByText(/answered/)).toBeNull()
  // Not stored at all (`recordAnswer` toasts and answers null): never counted.
  await act(async () => { settle(null) })
  expect(screen.queryByText(/answered/)).toBeNull()

  await answer()
  expect(await screen.findByText(/1 answered/)).toBeInTheDocument()
})

it('shows a dismissed check-in again for the session that replaces a closed one', async () => {
  // The dismissal belongs to the session: a closed one takes it with the rest of its state.
  mockApi({
    ...ROUTES,
    // A real start is a round trip, so the page renders with no session in between.
    'POST /api/sessions/start': () => new Promise(r => setTimeout(() => r({ id: 'sess-goal' }), 20)),
    'GET /api/profile/me': () => ({ id: 'u1', role: 'student', grade_level: '1st Grade' }),
    'GET /api/generate-question?bias=0&grade=1st+Grade&session_id=sess-goal': () => QUESTION,
  })
  const answer = async () => {
    await userEvent.click(await screen.findByRole('button', { name: /generate question|next question/i }))
    await userEvent.click(await screen.findByRole('button', { name: /^B\s*2$/ }))
    await userEvent.click(screen.getByRole('button', { name: /submit answer/i }))
    await screen.findByRole('button', { name: /next question/i })
  }
  const checkIn = /That is 5 questions answered/
  render(<Adaptive />)
  await screen.findByText(/how many questions/i)
  await userEvent.click(screen.getByRole('button', { name: '5' }))
  for (let i = 0; i < 5; i++) await answer()
  await userEvent.click(await screen.findByRole('button', { name: /keep going/i }))
  expect(screen.queryByText(checkIn)).toBeNull()

  recordAnswer.mockResolvedValueOnce({ ended: true })
  await answer()
  expect(await screen.findByText(checkIn)).toBeInTheDocument()
}, 20_000)

it('logs what came back when a generation answers 200 with no question', async () => {
  // No server log records a 200, so the page's own log is the only trace of what arrived.
  mockApi({
    ...ROUTES,
    'GET /api/profile/me': () => ({ id: 'u1', role: 'student', grade_level: '1st Grade' }),
    'GET /api/generate-question?bias=0&grade=1st+Grade&session_id=sess-goal': () => ({ detail: 'odd' }),
  })
  const logged = vi.spyOn(console, 'error').mockImplementation(() => {})
  try {
    render(<Adaptive />)
    await userEvent.click(await screen.findByRole('button', { name: /generate question/i }))
    await screen.findByText(/generation failed/i)
    const messages = logged.mock.calls.map(([e]) => String(e?.message ?? e))
    expect(messages.some(m => m.includes('No question_text') && m.includes('keys: detail'))).toBe(true)
  } finally {
    logged.mockRestore()
  }
})
