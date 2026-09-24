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

/** React may invoke a state updater more than once, so the timeout must not post from inside one. */
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

    // Question 1 of 10, so "Next", not "See Results".
    expect(await screen.findByRole('button', { name: /next/i })).toBeInTheDocument()
    // The cleared interval must not tick into negative time.
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

/** Advancing waits for an in-flight `/answer`, or `/end` can race it and the server drops the answer. */
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
  // `fireEvent.click` runs the handler synchronously; userEvent spans its own async boundaries.
  fireEvent.click(next)

  // The held answer is unresolved, so the next question must not be requested yet.
  expect(apiFetch).not.toHaveBeenCalledWith('/api/practice-sessions/sess-1/question')

  resolveAnswer()
  await vi.waitFor(() => {
    expect(apiFetch).toHaveBeenCalledWith('/api/practice-sessions/sess-1/question')
  })
})

/**
 * `handleNext` must not re-enter, or the index skips a question and wastes a generation.
 * A held answer keeps it suspended with the button mounted, the real double-click window.
 */
it('ignores a second click on Next while the first is still advancing', async () => {
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
  // Both land while `handleNext` is suspended on the held answer.
  fireEvent.click(next)
  fireEvent.click(next)
  expect(next).toBeDisabled()

  resolveAnswer()
  await vi.waitFor(() => {
    const questionCalls = apiFetch.mock.calls.filter(([path]) => path === '/api/practice-sessions/sess-1/question')
    expect(questionCalls).toHaveLength(1)
  })
  expect(await screen.findByText(/question 2 of 10/i)).toBeInTheDocument()
})

/** `questionCount` drives both the stopping rule and the "See Results" label. */
it('ends at a non-default question count, and labels the last question accordingly', async () => {
  const onFinish = vi.fn()
  render(<PracticeTest session={SESSION} onFinish={onFinish} questionCount={2} />)

  await screen.findByText('What is 2 + 2?')
  expect(screen.getByText(/question 1 of 2/i)).toBeInTheDocument()
  await userEvent.click(screen.getByRole('button', { name: /4/ }))

  mockApi({
    'GET /api/practice-sessions/sess-1/question': () => QUESTION_TWO,
    'POST /api/practice-sessions/sess-1/answer': () => ({ ok: true, topic: 'ordering' }),
  })
  await userEvent.click(await screen.findByRole('button', { name: /next/i }))

  await screen.findByText('What is 3 + 3?')
  expect(screen.getByText(/question 2 of 2/i)).toBeInTheDocument()
  expect(onFinish).not.toHaveBeenCalled()

  await userEvent.click(screen.getByRole('button', { name: /6/ }))
  await userEvent.click(await screen.findByRole('button', { name: /see results/i }))

  await vi.waitFor(() => expect(onFinish).toHaveBeenCalledWith({
    questions_answered: 2, correct_answers: 2,
  }))
})
