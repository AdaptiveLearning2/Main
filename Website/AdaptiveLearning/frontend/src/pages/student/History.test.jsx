import { it, expect, beforeEach, vi } from 'vitest'
import { render, screen, waitFor, act } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

// The shared router: the page reads two endpoints.
vi.mock('../../lib/api', async () => await import('../../test/mocks/apiFetch'))

import { apiFetch, mockApi, overrideApi, resetApi, apiError, pending } from '../../test/mocks/apiFetch'
import History from './History'

// A failed request must not look like an empty list.

const SESSION = {
  id: 's1', started_at: '2026-08-15T10:00:00Z', ended_at: '2026-08-15T10:30:00Z',
  questions_answered: 6, correct_answers: 4,
}

// `/api/sessions` answers a capped page plus the real total and a truncation flag.
const page = (sessions, extra = {}) => ({
  sessions, total: sessions.length, truncated: false, ...extra,
})

// Lifetime figures unlike anything the rows sum to.
const STATS = { total_questions: 431, total_correct: 302, retrieved: true }

const THREE = [
  { ...SESSION, id: 's1' },
  { ...SESSION, id: 's2', questions_answered: 4, correct_answers: 2 },
  { ...SESSION, id: 's3', questions_answered: 5, correct_answers: 5 },
]

// The figure above a tile's label; scoped, since rows repeat the same small numbers.
const tile = label =>
  screen.getByText(label).previousElementSibling.textContent

const serve = (sessions, stats = STATS) => mockApi({
  '/api/sessions': () => sessions,
  '/api/stats/me': () => stats,
})

beforeEach(() => { resetApi() })

it('says the read failed rather than claiming there are no sessions', async () => {
  serve(null)
  overrideApi('/api/sessions', () => { throw apiError(500, 'backend down') })

  render(<History />)

  expect(await screen.findByText(/couldn't load your session history/i)).toBeInTheDocument()
  expect(screen.queryByText(/no sessions here/i)).not.toBeInTheDocument()
})

it('still reports a genuinely empty history as empty', async () => {
  serve(page([]))

  render(<History />)

  expect(await screen.findByText(/no sessions here/i)).toBeInTheDocument()
  expect(screen.queryByText(/couldn't load/i)).not.toBeInTheDocument()
})

it('treats a body it does not recognise as a failed read, not as no sessions', async () => {
  // The bare list an older backend sends.
  serve([SESSION])

  render(<History />)

  expect(await screen.findByText(/couldn't load your session history/i)).toBeInTheDocument()
  expect(screen.queryByText(/no sessions here/i)).not.toBeInTheDocument()
})

it('retries without a page reload', async () => {
  serve(page([SESSION]))
  let fail = true
  overrideApi('/api/sessions', () => {
    if (fail) throw apiError(500, 'transient')
    return page([SESSION])
  })
  render(<History />)
  await screen.findByText(/couldn't load your session history/i)

  fail = false
  await userEvent.click(screen.getByRole('button', { name: /try again/i }))

  await waitFor(() =>
    expect(screen.queryByText(/couldn't load/i)).not.toBeInTheDocument())
  expect(apiFetch.mock.calls.filter(([p]) => p === '/api/sessions')).toHaveLength(2)
})

it('does not show the summary tiles when the read failed', async () => {
  serve(null)
  overrideApi('/api/sessions', () => { throw apiError(500, 'backend down') })

  render(<History />)

  await screen.findByText(/couldn't load/i)
  expect(screen.queryByText('Questions Done')).not.toBeInTheDocument()
  expect(screen.queryByText('Overall Accuracy')).not.toBeInTheDocument()
})

// ── the tiles are lifetime figures, and none of them is the list ──────────
// The list is the newest page of a longer history.

it('reports lifetime questions and accuracy, not a sum of the rows it was sent', async () => {
  serve(page(THREE))

  render(<History />)

  await screen.findByText('Total Sessions')
  expect(tile('Questions Done')).toBe('431')    // the rows sum to 15
  expect(tile('Overall Accuracy')).toBe('70%')  // the rows give 73%
})

it('shows the totals as still loading, not as unavailable, while they are in flight', async () => {
  // A dash means "could not be had", not "not finished".
  serve(page(THREE))
  overrideApi('/api/stats/me', pending())

  render(<History />)

  await screen.findByText('Total Sessions')
  expect(tile('Questions Done')).not.toBe('—')
  expect(tile('Overall Accuracy')).not.toBe('—')
  // A role, since a bare span's label is not read.
  expect(screen.getAllByRole('status', { name: 'Loading' })).toHaveLength(2)
})

it('keeps the totals a retry read when the first load fails late', async () => {
  // A superseded first totals read failing late must not reset the tiles.
  serve(page(THREE))
  let failFirstStats
  let statsCalls = 0
  overrideApi('/api/stats/me', () => {
    statsCalls += 1
    if (statsCalls === 1) return new Promise((_, reject) => { failFirstStats = reject })
    return STATS
  })
  let sessionCalls = 0
  overrideApi('/api/sessions', () => {
    sessionCalls += 1
    if (sessionCalls === 1) throw apiError(500, 'transient')
    return page(THREE)
  })
  render(<History />)
  await screen.findByText(/couldn't load your session history/i)

  await userEvent.click(screen.getByRole('button', { name: /try again/i }))
  await waitFor(() => expect(tile('Questions Done')).toBe('431'))

  // Inside act, so any stale write renders before the assertions.
  await act(async () => {
    failFirstStats(apiError(500, 'late'))
    await new Promise(r => setTimeout(r, 0))
  })
  expect(tile('Questions Done')).toBe('431')
  expect(tile('Overall Accuracy')).toBe('70%')
})

it.each([
  ['a missing question count', { total_correct: 3, retrieved: true }],
  ['a missing correct count',  { total_questions: 10, retrieved: true }],
])('does not turn %s into a zero', async (_name, stats) => {
  // An absence is never a zero.
  serve(page(THREE), stats)

  render(<History />)

  await screen.findByText('Total Sessions')
  expect(tile('Overall Accuracy')).toBe('—')
  if (!('total_questions' in stats)) expect(tile('Questions Done')).toBe('—')
})

it('has no accuracy to show for no questions, rather than 0%', async () => {
  serve(page(THREE), { total_questions: 0, total_correct: 0, retrieved: true })

  render(<History />)

  await screen.findByText('Total Sessions')
  expect(tile('Questions Done')).toBe('0')
  expect(tile('Overall Accuracy')).toBe('—')
})

it('says nothing about questions or accuracy when the totals could not be read', async () => {
  serve(page(THREE), { retrieved: false })

  render(<History />)

  await screen.findByText('Total Sessions')
  expect(tile('Questions Done')).toBe('—')
  expect(tile('Overall Accuracy')).toBe('—')
})

it('says the list was cut, and still shows how many sessions there really are', async () => {
  serve({ sessions: THREE, total: 431, truncated: true })

  render(<History />)

  expect(await screen.findByText(/showing your 3 most recent sessions of 431/i))
    .toBeInTheDocument()
  expect(tile('Total Sessions')).toBe('431')
})

it('says nothing about a whole history', async () => {
  // Teeth for the notice above.
  serve({ sessions: THREE, total: 3, truncated: false })

  render(<History />)

  await screen.findByText('Total Sessions')
  expect(screen.queryByText(/most recent sessions/i)).not.toBeInTheDocument()
})

it('claims no count at all when the backend could not count', async () => {
  // `total: null`: the count did not come back, so the rows' length is not a total.
  serve({ sessions: THREE, total: null, truncated: null })

  render(<History />)

  await screen.findByText('Total Sessions')
  expect(tile('Total Sessions')).toBe('—')
  expect(screen.queryByText(/most recent sessions/i)).not.toBeInTheDocument()
})
