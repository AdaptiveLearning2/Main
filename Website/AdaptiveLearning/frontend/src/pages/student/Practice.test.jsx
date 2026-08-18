import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'

vi.mock('../../lib/api', async () => await import('../../test/mocks/apiFetch'))
vi.mock('../../lib/supabase', async () => await import('../../test/mocks/supabase'))

const toastError = vi.fn()
vi.mock('sonner', () => ({ toast: { error: (...a) => toastError(...a), success: vi.fn() } }))

// `endSession` is its own module and posts on its own; these tests are about
// starting and answering, so it is stubbed to stop a failing close showing up
// as a failure of something else.
vi.mock('../../lib/session', () => ({ endSession: vi.fn().mockResolvedValue({}) }))

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
  resetApi()
  resetSupabaseMock()
  toastError.mockReset()
  setSession(buildAuthSession({ accessToken: 't' }))
  // The whole happy path once. Each test below overrides the single endpoint
  // it is about, rather than restating the rest -- which is what the router
  // exists for.
  mockApi({
    'POST /api/sessions/start': () => ({ id: 'sess-1' }),
    [QUESTIONS_PATH]: () => [QUESTION],
    'POST /api/sessions/sess-1/answer': () => ({ ok: true }),
  })
})

describe('starting a session', () => {
  it('renders the question once the session starts', async () => {
    draw()
    expect(await screen.findByText('What is 2 + 2?')).toBeInTheDocument()
  })

  it('says the session could not be started, rather than that there are no questions', async () => {
    // The two are one state apart and mean opposite things. A failed start
    // leaves `questions` empty, so the length check fired first and told a
    // student the question bank was empty whenever the backend was simply
    // unreachable -- an absence asserted from data that never came back.
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
    // The hang. `loading` starts true and the old code only cleared it inside
    // `startSession`, which a signed-out student never reached -- so the page
    // showed its spinner indefinitely with no error, no timeout and nothing to
    // click. Escapable only by reloading, with nothing on screen saying so.
    setSession(null)

    draw()

    expect(await screen.findByText(/couldn't load this practice session/i)).toBeInTheDocument()
    // And it never asked for a session, which is the half that says the page
    // stopped rather than failed later.
    expect(apiFetch).not.toHaveBeenCalledWith('/api/sessions/start', expect.anything())
  })

  it('survives a question the backend sent without any options', async () => {
    // The crash. The render maps `q.options` and indexes it in three more
    // places, none of them guarded, so one row like this threw during render --
    // and with no boundary above it that took the whole application down to a
    // blank document.
    overrideApi(QUESTIONS_PATH, () => ([
      { id: 'bad', question_text: 'no options here', correct_answer: 'x' },
      QUESTION,
    ]))

    draw()

    // The usable one is still offered, and the broken one is simply not there.
    expect(await screen.findByText('What is 2 + 2?')).toBeInTheDocument()
    expect(screen.queryByText('no options here')).not.toBeInTheDocument()
  })

  it('falls back to the empty state when nothing is answerable', async () => {
    // Dropping unanswerable rows must not turn "the bank has nothing usable"
    // into a blank screen -- the existing empty state is the right thing to
    // say, and this is the path that reaches it.
    overrideApi(QUESTIONS_PATH, () => ([{ id: 'bad', question_text: 'no options here' }]))

    draw()

    expect(await screen.findByText(/no questions available/i)).toBeInTheDocument()
  })
})

describe('answering', () => {
  it('tells the student when their answer could not be saved', async () => {
    // It had no catch at all, and the timeout path does not await it, so a
    // failed POST became an unhandled rejection: the screen moved on and the
    // answer was recorded nowhere, with nothing said.
    overrideApi('/api/sessions/sess-1/answer', () => { throw apiError(500, 'nope') }, 'POST')

    draw()
    await screen.findByText('What is 2 + 2?')

    await userEvent.click(screen.getByRole('button', { name: /4/ }))

    await waitFor(() => {
      expect(toastError).toHaveBeenCalledWith('That answer could not be saved.')
    })
  })

  it('says nothing when the answer saves', async () => {
    // The other half: a toast on every answer would train a student to ignore
    // the one that matters.
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
