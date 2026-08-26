import { it, expect, beforeEach, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
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

/**
 * The bug this pins: `postAnswer` was fired-and-forgotten from the timeout
 * path (it runs inside an effect, not an awaited event handler), so clicking
 * "Next"/"See Results" could call `onFinish` -- and, on the real page, reach
 * `/end` -- while the previous `/answer` POST was still unsettled. The
 * server's 409-on-ended guard then silently drops the late answer,
 * `recordPracticeAnswer` toasts a save failure right as results render, and
 * the results screen shows one more question answered than the server has.
 *
 * Reproduced here with a slow-POST probe: hold `/answer` unresolved, click
 * an option, then immediately click "Next" -- advancing must not proceed
 * until the held answer settles.
 */
it('waits for an in-flight answer to settle before advancing, so results cannot race it', async () => {
  let resolveAnswer
  const heldAnswer = new Promise(resolve => { resolveAnswer = resolve })
  mockApi({
    'GET /api/practice-sessions/sess-1/question': () => QUESTION_ONE,
    'POST /api/practice-sessions/sess-1/answer': () => heldAnswer.then(() => ({ ok: true, topic: 'ordering' })),
  })
  draw()
  await screen.findByText('What is 2 + 2?')

  await userEvent.click(screen.getByRole('button', { name: /4/ }))
  const next = await screen.findByRole('button', { name: /next/i })

  apiFetch.mockClear()
  // `fireEvent.click`, not `userEvent.click` -- userEvent's realistic pointer
  // sequence spans several of its own microtask/timer boundaries before the
  // click itself fires, which would make "check right after clicking" race
  // userEvent's own internals rather than the app's. `fireEvent.click`
  // invokes the React handler synchronously, so a synchronous `handleNext`
  // (the pre-fix shape) reaches its `apiFetch` call within this same call.
  fireEvent.click(next)

  // The held answer is still unresolved -- advancing must not have reached
  // the next question yet.
  expect(apiFetch).not.toHaveBeenCalledWith('/api/practice-sessions/sess-1/question')

  resolveAnswer()
  await vi.waitFor(() => {
    expect(apiFetch).toHaveBeenCalledWith('/api/practice-sessions/sess-1/question')
  })
})
