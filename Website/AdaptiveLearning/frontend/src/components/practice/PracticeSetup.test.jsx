import { it, expect, beforeEach, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

vi.mock('../../lib/api', async () => await import('../../test/mocks/apiFetch'))
vi.mock('sonner', () => ({ toast: { error: vi.fn(), success: vi.fn() } }))

import { apiFetch, mockApi, overrideApi, resetApi, apiError } from '../../test/mocks/apiFetch'
import PracticeSetup from './PracticeSetup'

const YOUNG_TOPICS = [
  { name: 'ordering', allowed: true },
  { name: 'algebra', allowed: false },
]
const OLD_TOPICS = [
  { name: 'ordering', allowed: true },
  { name: 'algebra', allowed: true },
]

const draw = (onStart = vi.fn()) => {
  const utils = render(<PracticeSetup onStart={onStart} />)
  return { onStart, ...utils }
}

beforeEach(() => {
  resetApi()
  mockApi({
    '/api/profile/me': () => ({ grade_level: '3rd Grade' }),
    'GET /api/topics?grade=3rd%20Grade': () => YOUNG_TOPICS,
    'GET /api/topics?grade=8th%20Grade': () => OLD_TOPICS,
    '/api/practice-sessions': () => [],
    'POST /api/practice-sessions/start': () => ({
      id: 'sess-1', mode: 'test', topics: ['ordering'], difficulty: 'medium',
    }),
  })
})

it('defaults the grade from the profile and greys out a disallowed topic', async () => {
  draw()
  expect(await screen.findByRole('button', { name: /algebra/i })).toBeDisabled()
  expect(screen.getByRole('button', { name: /ordering/i })).toBeEnabled()
})

it('clicking a disallowed topic does nothing', async () => {
  const { onStart } = draw()
  const algebra = await screen.findByRole('button', { name: /algebra/i })
  await userEvent.click(algebra)
  await userEvent.click(screen.getByRole('button', { name: /ordering/i }))
  await userEvent.click(screen.getByRole('button', { name: /start practice/i }))

  expect(onStart).toHaveBeenCalled()
  expect(apiFetch).toHaveBeenCalledWith('/api/practice-sessions/start', expect.objectContaining({
    body: expect.objectContaining({ topics: ['ordering'] }),
  }))
})

it('re-checks which topics are allowed when the grade changes, dropping a now-disallowed selection', async () => {
  draw()
  await screen.findByRole('button', { name: /algebra/i })

  await userEvent.selectOptions(screen.getByLabelText(/grade/i), '8th Grade')

  // algebra is allowed at 8th grade now
  expect(await screen.findByRole('button', { name: /algebra/i })).toBeEnabled()
})

it('picking a topic that becomes allowed does not resurrect a stale disabled state', async () => {
  draw()
  await screen.findByRole('button', { name: /ordering/i })
  await userEvent.click(screen.getByRole('button', { name: /ordering/i }))

  await userEvent.selectOptions(screen.getByLabelText(/grade/i), '8th Grade')
  await screen.findByRole('button', { name: /algebra/i, disabled: false })

  await userEvent.click(screen.getByRole('button', { name: /algebra/i }))
  await userEvent.click(screen.getByRole('button', { name: /start practice/i }))

  expect(apiFetch).toHaveBeenCalledWith('/api/practice-sessions/start', expect.objectContaining({
    body: expect.objectContaining({ topics: ['ordering', 'algebra'], grade: '8th Grade' }),
  }))
})

it('starts a test at 10 questions unless another count is picked', async () => {
  const { onStart } = draw()
  await userEvent.click(await screen.findByRole('button', { name: /ordering/i }))
  await userEvent.click(screen.getByRole('button', { name: /start practice/i }))

  expect(onStart).toHaveBeenCalledWith(expect.objectContaining({ id: 'sess-1' }), 10)
})

it('passes the picked question count to onStart, and sends nothing extra to the backend', async () => {
  const { onStart } = draw()
  await userEvent.click(await screen.findByRole('button', { name: /ordering/i }))
  await userEvent.click(screen.getByRole('button', { name: '20 questions' }))
  await userEvent.click(screen.getByRole('button', { name: /start practice/i }))

  expect(onStart).toHaveBeenCalledWith(expect.objectContaining({ id: 'sess-1' }), 20)
  // The count is a client-side stopping rule; the backend would not read it.
  const [, opts] = apiFetch.mock.calls.find(([path]) => path === '/api/practice-sessions/start')
  expect(opts.body).not.toHaveProperty('questionCount')
  expect(opts.body).not.toHaveProperty('question_count')
})

/** Flashcards have no deck size, so no count picker. */
it('hides the question count in flashcard mode', async () => {
  draw()
  await screen.findByRole('button', { name: /ordering/i })
  expect(screen.getByRole('button', { name: '10 questions' })).toBeInTheDocument()

  await userEvent.click(screen.getByRole('button', { name: /flashcards/i }))
  expect(screen.queryByRole('button', { name: '10 questions' })).not.toBeInTheDocument()
  expect(screen.queryByText(/how many questions/i)).not.toBeInTheDocument()
})

it('says the topics could not be loaded, and a retry asks again', async () => {
  let fail = true
  overrideApi('/api/topics?grade=3rd%20Grade', () => {
    if (fail) throw apiError(500, 'down')
    return YOUNG_TOPICS
  }, 'GET')
  draw()

  expect(await screen.findByText(/couldn't load topics/i)).toBeInTheDocument()
  expect(screen.getByRole('button', { name: /pick at least one topic/i })).toBeDisabled()

  fail = false
  await userEvent.click(screen.getByRole('button', { name: /try again/i }))
  expect(await screen.findByRole('button', { name: /ordering/i })).toBeEnabled()
})

it('never sends a pick the new grade does not allow', async () => {
  overrideApi('/api/topics?grade=8th%20Grade', () => [
    { name: 'ordering', allowed: false }, { name: 'algebra', allowed: true },
  ], 'GET')
  draw()
  await userEvent.click(await screen.findByRole('button', { name: /ordering/i }))

  await userEvent.selectOptions(screen.getByLabelText(/grade/i), '8th Grade')
  await userEvent.click(await screen.findByRole('button', { name: /algebra/i, disabled: false }))
  await userEvent.click(screen.getByRole('button', { name: /start practice/i }))

  const [, opts] = apiFetch.mock.calls.find(([path]) => path === '/api/practice-sessions/start')
  expect(opts.body.topics).toEqual(['algebra'])
})
