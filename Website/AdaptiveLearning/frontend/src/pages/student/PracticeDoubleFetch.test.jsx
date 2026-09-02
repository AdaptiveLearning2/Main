/**
 * One question per session opening, not two.
 *
 * Both practice modes loaded with `useEffect(() => { load() }, [load])` and no
 * guard. `<React.StrictMode>` invokes mount effects twice, so starting practice
 * made two `/question` calls: the student saw the first question appear and
 * vanish before it could be answered, and each call is two billed model calls
 * -- a topic decision and a generation -- so it doubled the cost of opening
 * every session.
 *
 * These render under StrictMode *explicitly*, because the app does
 * (`main.jsx`) and the other test files do not. Without that the guard could
 * be reverted and every existing test would still pass: the symptom is
 * dev-only, so a revert is invisible on a deployed site while costing two
 * generations per session on every developer's machine.
 *
 * Counting requests rather than asserting on the screen is the other half. The
 * `requestRef` guard means the *second* response never renders, so the visible
 * result is correct either way -- only the call count can tell whether the
 * second request was made at all.
 */
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
