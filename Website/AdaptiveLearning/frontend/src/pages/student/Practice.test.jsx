import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'

vi.mock('../../lib/api', async () => await import('../../test/mocks/apiFetch'))
vi.mock('../../lib/supabase', async () => await import('../../test/mocks/supabase'))
vi.mock('../../context/AuthContext', () => ({
  useAuth: () => ({ user: { id: 'student-1', email: 'kid@example.com' } }),
}))

const toastError = vi.fn()
vi.mock('sonner', () => ({ toast: { error: (...a) => toastError(...a), success: vi.fn() } }))

// `lib/practiceSession` is deliberately not mocked, since `recordPracticeAnswer`
// owns the failure toast these tests assert on -- stubbing it would just test
// the stub, the same reasoning Practice.jsx's old test file used for `lib/session`.

import { apiFetch, mockApi, overrideApi, resetApi, apiError } from '../../test/mocks/apiFetch'
import Practice from './Practice'

const TOPICS = [
  { name: 'ordering', allowed: true },
  { name: 'algebra', allowed: false },
]

const QUESTION = {
  id: 'q1',
  question_text: 'What is 2 + 2?',
  question_topic: 'ordering',
  answer_options: ['3', '4', '5'],
  correct_answer: '4',
  difficulty: 'easy',
}

const draw = () => render(<MemoryRouter><Practice /></MemoryRouter>)

beforeEach(() => {
  vi.useRealTimers()
  resetApi()
  toastError.mockReset()
  mockApi({
    '/api/profile/me': () => ({ grade_level: '5th Grade' }),
    'GET /api/topics?grade=5th%20Grade': () => TOPICS,
    '/api/practice-sessions': () => [],
    'POST /api/practice-sessions/start': () => ({
      id: 'sess-1', mode: 'test', topics: ['ordering'], difficulty: 'medium',
      grade_level: '5th Grade', questions_answered: 0, correct_answers: 0,
    }),
    'GET /api/practice-sessions/sess-1/question': () => QUESTION,
    'POST /api/practice-sessions/sess-1/answer': () => ({ ok: true, topic: 'ordering' }),
    'POST /api/practice-sessions/sess-1/end': () => ({
      ok: true, topic_summary: { ordering: { attempted: 1, correct: 100 } },
    }),
  })
})

async function startATestSession() {
  draw()
  await screen.findByText(/pick what to study/i)
  await userEvent.click(await screen.findByRole('button', { name: /ordering/i }))
  await userEvent.click(screen.getByRole('button', { name: /start practice/i }))
}

describe('setup', () => {
  it('greys out a topic the grade may not see, and blocks starting on it', async () => {
    draw()
    const algebra = await screen.findByRole('button', { name: /algebra/i })
    expect(algebra).toBeDisabled()
  })

  it('will not start without at least one topic picked', async () => {
    draw()
    await screen.findByText(/pick what to study/i)
    expect(screen.getByRole('button', { name: /pick at least one topic/i })).toBeDisabled()
  })

  it('starts a session with the picked topic, difficulty and mode', async () => {
    await startATestSession()
    expect(await screen.findByText('What is 2 + 2?')).toBeInTheDocument()
    expect(apiFetch).toHaveBeenCalledWith('/api/practice-sessions/start', {
      method: 'POST',
      body: { mode: 'test', topics: ['ordering'], difficulty: 'medium', grade: '5th Grade' },
    })
  })

  it('reports a failed setup load instead of an empty picker', async () => {
    overrideApi('/api/profile/me', () => { throw apiError(500, 'down') })
    draw()
    expect(await screen.findByText(/couldn't load practice setup/i)).toBeInTheDocument()
  })
})

describe('test mode', () => {
  it('scores an answer and moves on', async () => {
    await startATestSession()
    await screen.findByText('What is 2 + 2?')

    await userEvent.click(screen.getByRole('button', { name: /4/ }))

    await waitFor(() => {
      expect(apiFetch).toHaveBeenCalledWith(
        '/api/practice-sessions/sess-1/answer',
        expect.objectContaining({ method: 'POST' }))
    })
    expect(toastError).not.toHaveBeenCalled()
  })

  it('tells the student when their answer could not be saved', async () => {
    overrideApi('/api/practice-sessions/sess-1/answer', () => { throw apiError(500, 'nope') }, 'POST')
    await startATestSession()
    await screen.findByText('What is 2 + 2?')

    await userEvent.click(screen.getByRole('button', { name: /4/ }))

    await waitFor(() => {
      expect(toastError).toHaveBeenCalledWith('That answer could not be saved.')
    })
  })
})

// The results screen itself (score, topic breakdown, AI study tips) is
// exercised directly against a finished session in PracticeResults.test.jsx,
// rather than driving a full ten-question test through this page just to
// reach it.
