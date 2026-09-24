/** One `/question` call per opening under explicit StrictMode, counted by request since the screen looks right either way. */
import { StrictMode } from 'react'
import { it, expect, beforeEach, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'

vi.mock('../../lib/api', async () => await import('../../test/mocks/apiFetch'))
vi.mock('sonner', () => ({ toast: { error: vi.fn(), success: vi.fn() } }))

import { mockApi, overrideApi, resetApi } from '../../test/mocks/apiFetch'
import PracticeTest from './PracticeTest'
import PracticeFlashcards from './PracticeFlashcards'

const SESSION = { id: 'sess-1', mode: 'test' }
const QUESTION = {
  id: 'q1', question_text: 'What is 2 + 2?', question_topic: 'ordering',
  answer_options: ['3', '4', '5'], correct_answer: '4', difficulty: 'easy',
}

beforeEach(() => {
  vi.useRealTimers()
  resetApi()
  mockApi({
    'GET /api/practice-sessions/sess-1/question': () => QUESTION,
    'POST /api/practice-sessions/sess-1/answer': () => ({ ok: true, topic: 'ordering' }),
    'POST /api/practice-sessions/sess-1/view': () => ({ ok: true }),
  })
})

const countQuestionRequests = () => {
  let calls = 0
  overrideApi('/api/practice-sessions/sess-1/question', () => { calls += 1; return QUESTION })
  return () => calls
}

it('asks the backend for one question when a test session opens', async () => {
  const calls = countQuestionRequests()
  render(<StrictMode><PracticeTest session={SESSION} onFinish={vi.fn()} /></StrictMode>)
  await screen.findByText('What is 2 + 2?')
  await waitFor(() => expect(calls()).toBe(1))
})

it('asks the backend for one card when a flashcard session opens', async () => {
  const calls = countQuestionRequests()
  render(<StrictMode><PracticeFlashcards session={{ ...SESSION, mode: 'flashcards' }}
                                         onFinish={vi.fn()} /></StrictMode>)
  await screen.findByText('What is 2 + 2?')
  await waitFor(() => expect(calls()).toBe(1))
})
