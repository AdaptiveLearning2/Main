import { it, expect, beforeEach, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

vi.mock('../../lib/api', async () => await import('../../test/mocks/apiFetch'))
vi.mock('sonner', () => ({ toast: { error: vi.fn(), success: vi.fn() } }))

import { apiFetch, mockApi, resetApi } from '../../test/mocks/apiFetch'
import PracticeTest from './PracticeTest'

const SESSION = { id: 'sess-1', mode: 'test' }

const QUESTION_ONE = {
  id: 'q1', question_text: 'What is 2 + 2?', question_topic: 'ordering',
  answer_options: ['3', '4', '5'], correct_answer: '4', difficulty: 'easy',
}
const QUESTION_TWO = {
  id: 'q2', question_text: 'What is 3 + 3?', question_topic: 'ordering',
  answer_options: ['5', '6', '7'], correct_answer: '6', difficulty: 'easy',
}

const draw = (onFinish = vi.fn()) => {
  const utils = render(<PracticeTest session={SESSION} onFinish={onFinish} />)
  return { onFinish, ...utils }
}

beforeEach(() => {
  vi.useRealTimers()
  resetApi()
  mockApi({
    'GET /api/practice-sessions/sess-1/question': () => QUESTION_ONE,
    'POST /api/practice-sessions/sess-1/answer': () => ({ ok: true, topic: 'ordering' }),
  })
})

/**
 * The bug this pins: `setTimeLeft`'s updater function used to call
 * `handleTimeout()` itself, and React is free to invoke a state updater more
 * than once for one transition (StrictMode's dev double-invoke is one
 * trigger, not the only one) -- each extra call read a stale `revealed`
 * closure and posted a second "timed out" answer. A single 60s timeout must
 * record exactly one answer, with `selected_index: -1` and `correct: false`.
 */
it('records exactly one answer when the clock runs out', async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true })
  try {
    draw()
    await vi.waitFor(() => expect(screen.getByText('What is 2 + 2?')).toBeInTheDocument())

    await vi.advanceTimersByTimeAsync(60_000)

    await vi.waitFor(() => {
      const answerCalls = apiFetch.mock.calls.filter(([path]) => path === '/api/practice-sessions/sess-1/answer')
      expect(answerCalls).toHaveLength(1)
      expect(answerCalls[0][1]).toEqual(expect.objectContaining({
        method: 'POST',
        body: { question_id: 'q1', selected_index: -1, correct: false },
      }))
    })
  } finally {
    vi.useRealTimers()
  }
})

it('reveals the correct answer once the clock runs out, and stops the countdown', async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true })
  try {
    draw()
    await vi.waitFor(() => expect(screen.getByText('What is 2 + 2?')).toBeInTheDocument())

    await vi.advanceTimersByTimeAsync(60_000)

    // Question 1 of 10, so the reveal button reads "Next", not "See Results".
    expect(await screen.findByRole('button', { name: /next/i })).toBeInTheDocument()
    // Nothing further ticks the (now-cleared) interval into negative time.
    await vi.advanceTimersByTimeAsync(5_000)
    expect(screen.getByText('⏱ 0s')).toBeInTheDocument()
  } finally {
    vi.useRealTimers()
  }
})

it('records exactly one answer for a clicked option, even if clicked twice', async () => {
  draw()
  await screen.findByText('What is 2 + 2?')

  const four = screen.getByRole('button', { name: /4/ })
  await userEvent.click(four)
  await userEvent.click(four)

  const answerCalls = apiFetch.mock.calls.filter(([path]) => path === '/api/practice-sessions/sess-1/answer')
  expect(answerCalls).toHaveLength(1)
  expect(answerCalls[0][1].body).toEqual({ question_id: 'q1', selected_index: 1, correct: true })
})

it('resets the answered guard and the timer on the next question', async () => {
  mockApi({
    'GET /api/practice-sessions/sess-1/question': () => QUESTION_ONE,
    'POST /api/practice-sessions/sess-1/answer': () => ({ ok: true, topic: 'ordering' }),
  })
  draw()
  await screen.findByText('What is 2 + 2?')
  await userEvent.click(screen.getByRole('button', { name: /4/ }))
  await screen.findByRole('button', { name: /next/i })

  // Swap in the second question for the "Next" click.
  mockApi({
    'GET /api/practice-sessions/sess-1/question': () => QUESTION_TWO,
    'POST /api/practice-sessions/sess-1/answer': () => ({ ok: true, topic: 'ordering' }),
  })
  apiFetch.mockClear()
  await userEvent.click(screen.getByRole('button', { name: /next/i }))

  await screen.findByText('What is 3 + 3?')
  expect(screen.getByText('⏱ 60s')).toBeInTheDocument()

  await userEvent.click(screen.getByRole('button', { name: /6/ }))
  const answerCalls = apiFetch.mock.calls.filter(([path]) => path === '/api/practice-sessions/sess-1/answer')
  expect(answerCalls).toHaveLength(1)
  expect(answerCalls[0][1].body).toEqual({ question_id: 'q2', selected_index: 1, correct: true })
})
