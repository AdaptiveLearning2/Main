import { it, expect, beforeEach, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'

vi.mock('../../lib/api', async () => await import('../../test/mocks/apiFetch'))
vi.mock('../../context/AuthContext', () => ({
  useAuth: () => ({ user: { id: 'student-1' } }),
}))

import { mockApi, overrideApi, resetApi, apiError } from '../../test/mocks/apiFetch'
import PracticeResults from './PracticeResults'

const TEST_SESSION = { id: 'sess-1', mode: 'test', questions_answered: 4, correct_answers: 3 }
const TEST_RESULT = { topic_summary: { ordering: { attempted: 4, correct: 75 } } }

const draw = (session = TEST_SESSION, result = TEST_RESULT) => render(
  <MemoryRouter><PracticeResults session={session} result={result} onRestart={vi.fn()} /></MemoryRouter>
)

beforeEach(() => {
  resetApi()
  mockApi({
    'POST /api/students/student-1/learning-strategies': () => ({
      strategies: ['Review ordering before new material.'],
      source: 'rule-based',
    }),
  })
})

it('shows the score and per-topic breakdown for a test session', () => {
  draw()
  expect(screen.getByText('You scored 3 out of 4')).toBeInTheDocument()
  expect(screen.getByText('75%')).toBeInTheDocument()
  expect(screen.getByText(/75% · 4 questions/)).toBeInTheDocument()
})

it('shows a reviewed count, not a score, for a flashcard session', () => {
  draw({ id: 'sess-1', mode: 'flashcard', questions_answered: 5, correct_answers: 0 },
        { topic_summary: { geometry: { attempted: 5, correct: null } } })
  expect(screen.getByText('You reviewed 5 cards')).toBeInTheDocument()
  // No graded rate for a viewed-only topic.
  expect(screen.getByText(/5 questions/)).toBeInTheDocument()
  expect(screen.queryByText(/%.*5 questions/)).not.toBeInTheDocument()
})

it('fetches study tips only when asked, scoped to this practice session', async () => {
  draw()
  expect(screen.queryByText(/review ordering/i)).not.toBeInTheDocument()

  await userEvent.click(screen.getByRole('button', { name: /get study tips/i }))

  expect(await screen.findByText('Review ordering before new material.')).toBeInTheDocument()
})

it('offers a retry when the tips call fails', async () => {
  overrideApi('/api/students/student-1/learning-strategies', () => { throw apiError(500, 'down') }, 'POST')
  draw()

  await userEvent.click(screen.getByRole('button', { name: /get study tips/i }))

  expect(await screen.findByText(/couldn't get study tips/i)).toBeInTheDocument()
})
