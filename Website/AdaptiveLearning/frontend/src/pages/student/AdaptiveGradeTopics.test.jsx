/** The page lists the topics the student's grade is served, and names a topic with every underscore a space. */
import { it, expect, beforeEach, vi } from 'vitest'
import { act, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

vi.mock('../../lib/api', async () => await import('../../test/mocks/apiFetch'))
vi.mock('../../lib/supabase', async () => await import('../../test/mocks/supabase'))
vi.mock('sonner', () => ({ toast: { error: vi.fn(), success: vi.fn() } }))
vi.mock('../../lib/session', () => ({
  endSession: vi.fn(async () => true),
  markEegStarted: vi.fn(async () => true),
  recordAnswer: vi.fn(async () => null),
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

import { apiFetch, mockApi, overrideApi, resetApi, apiError } from '../../test/mocks/apiFetch'
import Adaptive from './Adaptive'

const KINDERGARTEN = ['counting', 'comparing_numbers', 'add_and_subtract', 'teen_numbers', 'shapes']
// `/api/topics` answers for every topic; a sample of the others is enough to be wrong about.
const TOPICS_ROWS = [...KINDERGARTEN.map(name => ({ name, allowed: true })),
  { name: 'ordering', allowed: false }, { name: 'algebra', allowed: false }]

beforeEach(() => {
  resetApi()
  vi.clearAllMocks()
  // The page persists its mode; the class-mode test would leave every later one in it.
  localStorage.removeItem('adaptive_mode')
  mockApi({
    'GET /api/profile/me': () => ({ id: 'u1', role: 'student', grade_level: 'Kindergarten' }),
    'GET /api/performance/student/u1': () => [],
    'POST /api/sessions/start': () => ({ id: 'sess-k' }),
    'GET /api/eeg/health': () => ({ available: false }),
    'GET /api/topics?grade=Kindergarten': () => TOPICS_ROWS,
    'GET /api/generate-question?bias=0&grade=Kindergarten&session_id=sess-k': () => ({
      id: 'q1', question_text: 'What is 2 + 1 = ?', question_topic: 'add_and_subtract',
      answer_options: ['2', '3', '4'], correct_answer: '3', difficulty: 'easy',
    }),
  })
})

// Waits for that exact request to settle: until then the hook answers `null` either way.
async function settled(path) {
  await waitFor(() => expect(apiFetch).toHaveBeenCalledWith(path))
  const i = apiFetch.mock.calls.findIndex(([p]) => p === path)
  await act(async () => { await apiFetch.mock.results[i].value.catch(() => {}) })
}

function sidebar() {
  return screen.getByText('Topic Accuracy').parentElement
}

it("lists only the grade's topics in the accuracy sidebar, and says how many", async () => {
  render(<Adaptive />)
  await waitFor(() => expect(within(sidebar()).queryByText(/ordering/)).not.toBeInTheDocument())
  expect(within(sidebar()).getByText(/add and subtract/)).toBeInTheDocument()
  expect(within(sidebar()).queryByText(/algebra/)).not.toBeInTheDocument()
  expect(screen.getByText(/across 5 topics/)).toBeInTheDocument()
})

it('keeps a topic the student has attempted, whatever the grade', async () => {
  overrideApi('/api/performance/student/u1', () => [
    { correct_questions: 3, attempted_questions: 4, math_topics: { topic_name: 'algebra' } },
  ], 'GET')
  render(<Adaptive />)
  expect(await within(sidebar()).findByText(/algebra/)).toBeInTheDocument()
  await waitFor(() => expect(within(sidebar()).queryByText(/ordering/)).not.toBeInTheDocument())
})

it('lists every topic when the grade could not be looked up', async () => {
  overrideApi('/api/topics?grade=Kindergarten', () => { throw apiError(500, 'down') }, 'GET')
  render(<Adaptive />)
  await settled('/api/topics?grade=Kindergarten')
  expect(within(sidebar()).getByText(/ordering/)).toBeInTheDocument()
  expect(within(sidebar()).getByText(/add and subtract/)).toBeInTheDocument()
})

it('leaves a student with no grade to the backend default, naming no grade itself', async () => {
  overrideApi('/api/profile/me', () => ({ id: 'u1', role: 'student', grade_level: null }), 'GET')
  // No `grade` parameter on either read: the backend answers for its own default.
  overrideApi('/api/topics', () => TOPICS_ROWS, 'GET')
  overrideApi('/api/generate-question?bias=0&session_id=sess-k', () => ({
    id: 'q1', question_text: 'What is 2 + 1 = ?', question_topic: 'add_and_subtract',
    answer_options: ['2', '3', '4'], correct_answer: '3', difficulty: 'easy',
  }), 'GET')
  render(<Adaptive />)

  await settled('/api/profile/me')
  expect(screen.getByDisplayValue('Grade not set')).toBeInTheDocument()
  // Names no grade rather than one it would have to copy from the backend.
  expect(screen.getByText(/questions \(grade not set\)/)).toBeInTheDocument()
  await userEvent.click(await screen.findByRole('button', { name: /generate question/i }))
  expect(await screen.findByText('What is 2 + 1 = ?')).toBeInTheDocument()
  expect(apiFetch.mock.calls.some(([p]) => /[?&]grade=/.test(p))).toBe(false)
})

it('calls an account with no profile row "not set", not unknown', async () => {
  // A 404 answers the question: there is no saved grade.
  overrideApi('/api/profile/me', () => { throw apiError(404, 'No profile exists for this account') }, 'GET')
  overrideApi('/api/topics', () => TOPICS_ROWS, 'GET')
  render(<Adaptive />)

  await settled('/api/profile/me')
  expect(await screen.findByDisplayValue('Grade not set')).toBeInTheDocument()
  expect(screen.queryByText(/grade unknown/)).not.toBeInTheDocument()
})

it('says the grade is unknown, and lists no grade\'s topics, when the profile could not be read', async () => {
  // The backend reads the profile itself and may serve 7th grade; "not set" would be a guess.
  overrideApi('/api/profile/me', () => { throw apiError(500, 'down') }, 'GET')
  render(<Adaptive />)

  await settled('/api/profile/me')
  expect(screen.getByDisplayValue('Grade unknown')).toBeInTheDocument()
  expect(screen.getByText(/questions \(grade unknown\)/)).toBeInTheDocument()
  expect(screen.queryByText(/grade not set/)).not.toBeInTheDocument()
  expect(apiFetch.mock.calls.some(([p]) => p.startsWith('/api/topics'))).toBe(false)
})

it('calls the grade neither unknown nor unset while the profile is still loading', async () => {
  overrideApi('/api/profile/me', () => new Promise(() => {}), 'GET')
  render(<Adaptive />)

  await screen.findByDisplayValue('Loading grade…')
  const line = screen.getByText(/questions/, { selector: 'p' })
  expect(line).not.toHaveTextContent(/grade unknown|grade not set/)
})

it('reads a failed profile again, and then names the grade it found', async () => {
  let calls = 0
  overrideApi('/api/profile/me', () => {
    calls += 1
    if (calls === 1) throw apiError(500, 'down')
    return { id: 'u1', role: 'student', grade_level: 'Kindergarten' }
  }, 'GET')
  render(<Adaptive />)

  expect(await screen.findByText(/questions \(grade unknown\)/)).toBeInTheDocument()
  // The first retry waits a second.
  expect(await screen.findByText('Kindergarten', { selector: 'strong' })).toBeInTheDocument()
  expect(calls).toBe(2)
  expect(screen.queryByText(/grade unknown/)).not.toBeInTheDocument()
})

it('keeps a grade the student picked while the profile was being read again', async () => {
  let calls = 0
  overrideApi('/api/profile/me', () => {
    calls += 1
    if (calls === 1) throw apiError(500, 'down')
    return { id: 'u1', role: 'student', grade_level: 'Kindergarten' }
  }, 'GET')
  overrideApi('/api/topics?grade=3rd%20Grade', () => TOPICS_ROWS, 'GET')
  render(<Adaptive />)

  await userEvent.selectOptions(await screen.findByDisplayValue('Grade unknown'), '3rd Grade')
  await waitFor(() => expect(calls).toBe(2))
  const retry = apiFetch.mock.calls.findLastIndex(([p]) => p === '/api/profile/me')
  await act(async () => { await apiFetch.mock.results[retry].value })
  expect(screen.getByDisplayValue('3rd Grade')).toBeInTheDocument()
})

it('says so when a class has no grade and the student\'s could not be read', async () => {
  overrideApi('/api/profile/me', () => { throw apiError(500, 'down') }, 'GET')
  overrideApi('/api/classes', () => [{ id: 'c1', name: 'Maths', grade_level: null }], 'GET')
  render(<Adaptive />)

  await settled('/api/profile/me')
  // The Class button is disabled until the class list lands; a click before then does nothing.
  await settled('/api/classes')
  await userEvent.click(await screen.findByRole('button', { name: /class/i }))
  expect(await screen.findByText(/using your grade, which could not be loaded/)).toBeInTheDocument()
  expect(screen.getByText(/questions/, { selector: 'p' })).toHaveTextContent(/\(grade unknown\)/)
})

it("serves a class with no grade the student's saved grade, not the solo pick", async () => {
  overrideApi('/api/profile/me', () => ({ id: 'u1', role: 'student', grade_level: '4th Grade' }), 'GET')
  overrideApi('/api/classes', () => [{ id: 'c1', name: 'Maths', grade_level: null }], 'GET')
  overrideApi('/api/topics?grade=4th%20Grade', () => TOPICS_ROWS, 'GET')
  overrideApi('/api/topics?grade=2nd%20Grade', () => TOPICS_ROWS, 'GET')
  render(<Adaptive />)

  await settled('/api/profile/me')
  // A solo pick is not saved; the backend falls back to the profile's grade.
  await userEvent.selectOptions(screen.getByDisplayValue('4th Grade'), '2nd Grade')
  // The Class button is disabled until the class list lands; a click before then does nothing.
  await settled('/api/classes')
  await userEvent.click(await screen.findByRole('button', { name: /class/i }))

  expect(await screen.findByText(/using your grade, 4th Grade/)).toBeInTheDocument()
  expect(screen.getByText(/questions/, { selector: 'p' })).toHaveTextContent(/for 4th Grade/)
})

it('names a two-underscore topic with every underscore a space', async () => {
  render(<Adaptive />)
  await userEvent.click(await screen.findByRole('button', { name: /generate question/i }))
  await screen.findByText('What is 2 + 1 = ?')
  expect(screen.queryByText(/and_subtract/)).not.toBeInTheDocument()
  expect(screen.getAllByText(/add and subtract/).length).toBeGreaterThanOrEqual(2)
})
