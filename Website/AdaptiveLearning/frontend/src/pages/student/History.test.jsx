import { it, expect, beforeEach, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

// Through the shared router rather than a bare `vi.fn()`: the page reads two
// endpoints, and a `mockResolvedValue` would answer both with one body.
vi.mock('../../lib/api', async () => await import('../../test/mocks/apiFetch'))

import { apiFetch, mockApi, overrideApi, resetApi, apiError, pending } from '../../test/mocks/apiFetch'
import History from './History'

// A failed request must not look like an empty list -- "No sessions here" would
// be a false claim about a backend that's just unreachable. History stands in
// for other list pages sharing this shape; they all use the same LoadError
// component.

const SESSION = {
  id: 's1', started_at: '2026-08-15T10:00:00Z', ended_at: '2026-08-15T10:30:00Z',
  questions_answered: 6, correct_answers: 4,
}

// `/api/sessions` answers an object, not a list: the backend caps the read at
// `_SESSION_LIST_MAX`, so the page needs the real total and a flag saying
// whether what it was sent is all of it.
const page = (sessions, extra = {}) => ({
  sessions, total: sessions.length, truncated: false, ...extra,
})

// Lifetime figures, deliberately unlike anything the rows could sum to, so a
// tile reading the rows instead cannot pass by coincidence.
const STATS = { total_questions: 431, total_correct: 302, retrieved: true }

const THREE = [
  { ...SESSION, id: 's1' },
  { ...SESSION, id: 's2', questions_answered: 4, correct_answers: 2 },
  { ...SESSION, id: 's3', questions_answered: 5, correct_answers: 5 },
]

// The figure above a tile's label. Scoped, because the same small numbers
// appear on every session row below -- a bare `getByText('3')` matches a row's
// "correct" count as readily as the tile's.
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
  // A new student really has no sessions -- this must not be treated as a failure either.
  serve(page([]))

  render(<History />)

  expect(await screen.findByText(/no sessions here/i)).toBeInTheDocument()
  expect(screen.queryByText(/couldn't load/i)).not.toBeInTheDocument()
})

it('treats a body it does not recognise as a failed read, not as no sessions', async () => {
  // The bare list an older backend sends. Unwrapped as `r?.sessions || []` it
  // became a student who had done nothing.
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
//
// The list is the newest page of a longer history. A tile that counted or
// summed it would describe a subset while looking like a lifetime -- and past
// the cap that reads as a student who did less work than they did.

it('reports lifetime questions and accuracy, not a sum of the rows it was sent', async () => {
  serve(page(THREE))

  render(<History />)

  await screen.findByText('Total Sessions')
  expect(tile('Questions Done')).toBe('431')    // the rows sum to 15
  expect(tile('Overall Accuracy')).toBe('70%')  // the rows give 73%
})

it('shows the totals as still loading, not as unavailable, while they are in flight', async () => {
  // The list lands first. A dash here would say the figures could not be had
  // -- the same thing a failed read says -- for a read that has not finished.
  serve(page(THREE))
  overrideApi('/api/stats/me', pending())

  render(<History />)

  await screen.findByText('Total Sessions')
  expect(tile('Questions Done')).not.toBe('—')
  expect(tile('Overall Accuracy')).not.toBe('—')
  expect(screen.getAllByLabelText('Loading')).toHaveLength(2)
})

it.each([
  ['a missing question count', { total_correct: 3, retrieved: true }],
  ['a missing correct count',  { total_questions: 10, retrieved: true }],
])('does not turn %s into a zero', async (_name, stats) => {
  // Rule 2: an absence is never a zero. `?? 0` read a field the response did
  // not carry as a student who had answered nothing.
  serve(page(THREE), stats)

  render(<History />)

  await screen.findByText('Total Sessions')
  expect(tile('Overall Accuracy')).toBe('—')
  if (!('total_questions' in stats)) expect(tile('Questions Done')).toBe('—')
})

it('has no accuracy to show for no questions, rather than 0%', async () => {
  // 0% reads as every answer wrong. Zero questions answered has no accuracy.
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
  // The tile reports the real count, not the length of what was sent.
  expect(tile('Total Sessions')).toBe('431')
})

it('says nothing about a whole history', async () => {
  // Or the notice above is satisfied by a page that always draws it.
  serve({ sessions: THREE, total: 3, truncated: false })

  render(<History />)

  await screen.findByText('Total Sessions')
  expect(screen.queryByText(/most recent sessions/i)).not.toBeInTheDocument()
})

it('claims no count at all when the backend could not count', async () => {
  // `total: null` is the third state -- the count did not come back. The rows
  // it did send are a page of an unknown whole, so their length is not a
  // total, and a notice either way would be a claim nobody can back.
  serve({ sessions: THREE, total: null, truncated: null })

  render(<History />)

  await screen.findByText('Total Sessions')
  expect(tile('Total Sessions')).toBe('—')
  expect(screen.queryByText(/most recent sessions/i)).not.toBeInTheDocument()
})
