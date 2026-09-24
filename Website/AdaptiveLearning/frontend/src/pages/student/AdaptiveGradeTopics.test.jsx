/** The page lists the topics the student's grade is served, and names a topic with every underscore a space. */
import { it, expect, beforeEach, vi } from 'vitest'
import { act, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

vi.mock('../../lib/api', async () => await import('../../test/mocks/apiFetch'))
vi.mock('../../lib/supabase', async () => await import('../../test/mocks/supabase'))
vi.mock('sonner', () => ({ toast: { error: vi.fn(), success: vi.fn() } }))
vi.mock('../../lib/session', () => ({
  endSession: vi.fn(async () => true),
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

it('names a two-underscore topic with every underscore a space', async () => {
  render(<Adaptive />)
  await userEvent.click(await screen.findByRole('button', { name: /generate question/i }))
  await screen.findByText('What is 2 + 1 = ?')
  expect(screen.queryByText(/and_subtract/)).not.toBeInTheDocument()
  expect(screen.getAllByText(/add and subtract/).length).toBeGreaterThanOrEqual(2)
})
