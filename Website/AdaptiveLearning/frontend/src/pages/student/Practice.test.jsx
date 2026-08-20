import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'

vi.mock('../../lib/api', async () => await import('../../test/mocks/apiFetch'))
vi.mock('../../lib/supabase', async () => await import('../../test/mocks/supabase'))

const toastError = vi.fn()
vi.mock('sonner', () => ({ toast: { error: (...a) => toastError(...a), success: vi.fn() } }))

// `lib/session` is deliberately not mocked, since `recordAnswer` owns the
// failure toast these tests assert on -- stubbing it would just test the stub.

import { apiFetch, mockApi, overrideApi, resetApi, apiError } from '../../test/mocks/apiFetch'
import { buildAuthSession, resetSupabaseMock, setSession } from '../../test/mocks/supabase'
import Practice from './Practice'

const QUESTIONS_PATH = '/api/questions?limit=10'

const QUESTION = {
  id: 'q1',
  question_text: 'What is 2 + 2?',
  options: ['3', '4', '5'],
  correct_answer: '4',
  subject: 'algebra',
  difficulty: 'easy',
}

const draw = () => render(<MemoryRouter><Practice /></MemoryRouter>)

beforeEach(() => {
  // Reset real timers here (not just in the fake-timer test's own teardown),
  // so a mid-test failure there can't leave every later test waiting on a
  // clock that never advances.
  vi.useRealTimers()
  resetApi()
  resetSupabaseMock()
  toastError.mockReset()
  setSession(buildAuthSession({ accessToken: 't' }))
  // The happy path. Each test below overrides just the endpoint it's testing.
  mockApi({
    'POST /api/sessions/start': () => ({ id: 'sess-1' }),
    [QUESTIONS_PATH]: () => [QUESTION],
    'POST /api/sessions/sess-1/answer': () => ({ ok: true }),
    // Reached by the real `endSession` at the end of a run, so it succeeds
    // quietly instead of toasting an unrelated close failure.
    'POST /api/sessions/sess-1/end': () => ({ ok: true }),
  })
})

describe('starting a session', () => {
  it('renders the question once the session starts', async () => {
    draw()
    expect(await screen.findByText('What is 2 + 2?')).toBeInTheDocument()
  })

  it('says the session could not be started, rather than that there are no questions', async () => {
    // A failed start also leaves `questions` empty, so this must not be
    // reported as "no questions available".
    overrideApi('/api/sessions/start', () => { throw apiError(500, 'down') }, 'POST')

    draw()

    expect(await screen.findByText(/couldn't load this practice session/i)).toBeInTheDocument()
    expect(screen.queryByText(/no questions available/i)).not.toBeInTheDocument()
  })

  it('offers a retry that actually re-requests', async () => {
    let attempts = 0
    overrideApi('/api/sessions/start', () => {
      attempts += 1
      if (attempts === 1) throw apiError(500, 'down')
      return { id: 'sess-1' }
    }, 'POST')

    draw()
    await screen.findByText(/couldn't load this practice session/i)

    await userEvent.click(screen.getByRole('button', { name: /try again/i }))

    expect(await screen.findByText('What is 2 + 2?')).toBeInTheDocument()
  })

  it('does not sit on the spinner for ever when there is no session', async () => {
    setSession(null)

    draw()

    expect(await screen.findByText(/couldn't load this practice session/i)).toBeInTheDocument()
    // Confirms the page stopped early rather than attempting and failing later.
    expect(apiFetch).not.toHaveBeenCalledWith('/api/sessions/start', expect.anything())
  })

  it('survives a question the backend sent without any options', async () => {
    // A question with no options would crash the render if not filtered out.
    overrideApi(QUESTIONS_PATH, () => ([
      { id: 'bad', question_text: 'no options here', correct_answer: 'x' },
      QUESTION,
    ]))

    draw()

    // The usable question still renders; the broken one is dropped.
    expect(await screen.findByText('What is 2 + 2?')).toBeInTheDocument()
    expect(screen.queryByText('no options here')).not.toBeInTheDocument()
  })

  it('drops a question that cannot be got right', async () => {
    // A question with no correct_answer is unwinnable no matter what's picked,
    // since `normalize(null)` matches no real option.
    overrideApi(QUESTIONS_PATH, () => ([
      { id: 'bad', question_text: 'unwinnable', options: ['a', 'b'] },
      QUESTION,
    ]))

    draw()

    expect(await screen.findByText('What is 2 + 2?')).toBeInTheDocument()
    expect(screen.queryByText('unwinnable')).not.toBeInTheDocument()
  })

  it('does not leave the retried question already revealed', async () => {
    // Confirms the countdown doesn't run (and pre-reveal the next question)
    // while the failed-start screen has no question to time.
    // `shouldAdvanceTime` lets the clock move on its own, since the initial
    // fetches this page awaits depend on real timer progress.
    vi.useFakeTimers({ shouldAdvanceTime: true })
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
    try {
      let attempts = 0
      overrideApi('/api/sessions/start', () => {
        attempts += 1
        if (attempts === 1) throw apiError(500, 'down')
        return { id: 'sess-1' }
      }, 'POST')

      draw()
      await vi.waitFor(() =>
        expect(screen.getByText(/couldn't load this practice session/i)).toBeInTheDocument())

      // Longer than TIMER, on the screen that has nothing to time.
      await vi.advanceTimersByTimeAsync(61_000)

      await user.click(screen.getByRole('button', { name: /try again/i }))
      await vi.waitFor(() =>
        expect(screen.getByText('What is 2 + 2?')).toBeInTheDocument())

      expect(screen.getByRole('button', { name: /4/ })).toBeEnabled()
    } finally {
      vi.useRealTimers()
    }
  })

  it('falls back to the empty state when nothing is answerable', async () => {
    // When every question is filtered out, show the empty state, not a blank screen.
    overrideApi(QUESTIONS_PATH, () => ([{ id: 'bad', question_text: 'no options here' }]))

    draw()

    expect(await screen.findByText(/no questions available/i)).toBeInTheDocument()
  })
})

describe('answering', () => {
  it('tells the student when their answer could not be saved', async () => {
    // A failed save must surface as a toast, not silently go nowhere.
    overrideApi('/api/sessions/sess-1/answer', () => { throw apiError(500, 'nope') }, 'POST')

    draw()
    await screen.findByText('What is 2 + 2?')

    await userEvent.click(screen.getByRole('button', { name: /4/ }))

    await waitFor(() => {
      expect(toastError).toHaveBeenCalledWith('That answer could not be saved.')
    })
  })

  it('says nothing when the answer saves', async () => {
    // A toast on every successful answer would train students to ignore the one that matters.
    draw()
    await screen.findByText('What is 2 + 2?')

    await userEvent.click(screen.getByRole('button', { name: /4/ }))

    await waitFor(() => {
      expect(apiFetch).toHaveBeenCalledWith(
        '/api/sessions/sess-1/answer', expect.objectContaining({ method: 'POST' }))
    })
    expect(toastError).not.toHaveBeenCalled()
  })
})
