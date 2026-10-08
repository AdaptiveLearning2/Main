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

// A `practice_sessions` row as `GET /api/practice-sessions` returns it (`select *`).
const PRACTICE = {
  id: 'p1', user_id: 'u1', mode: 'test', topics: ['ordering'], difficulty: 'easy', grade_level: '6th Grade',
  started_at: '2026-08-16T09:00:00Z', ended_at: '2026-08-16T09:20:00Z',
  questions_answered: 10, correct_answers: 8, topic_summary: {},
}

const serve = (sessions, stats = STATS, practice = []) => mockApi({
  '/api/sessions': () => sessions,
  '/api/stats/me': () => stats,
  '/api/practice-sessions': () => practice,
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
  expect(screen.queryByText('Adaptive Questions')).not.toBeInTheDocument()
  expect(screen.queryByText('Adaptive Accuracy')).not.toBeInTheDocument()
})

// ── the tiles are lifetime figures, and none of them is the list ──────────
// The list is the newest page of a longer history.

it('reports lifetime questions and accuracy, not a sum of the rows it was sent', async () => {
  serve(page(THREE))

  render(<History />)

  await screen.findByText('Adaptive Sessions')
  expect(tile('Adaptive Questions')).toBe('431')    // the rows sum to 15
  expect(tile('Adaptive Accuracy')).toBe('70%')  // the rows give 73%
})

it('shows the totals as still loading, not as unavailable, while they are in flight', async () => {
  // A dash means "could not be had", not "not finished".
  serve(page(THREE))
  overrideApi('/api/stats/me', pending())

  render(<History />)

  await screen.findByText('Adaptive Sessions')
  expect(tile('Adaptive Questions')).not.toBe('—')
  expect(tile('Adaptive Accuracy')).not.toBe('—')
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
  await waitFor(() => expect(tile('Adaptive Questions')).toBe('431'))

  // Inside act, so any stale write renders before the assertions.
  await act(async () => {
    failFirstStats(apiError(500, 'late'))
    await new Promise(r => setTimeout(r, 0))
  })
  expect(tile('Adaptive Questions')).toBe('431')
  expect(tile('Adaptive Accuracy')).toBe('70%')
})

it.each([
  ['a missing question count', { total_correct: 3, retrieved: true }],
  ['a missing correct count',  { total_questions: 10, retrieved: true }],
])('does not turn %s into a zero', async (_name, stats) => {
  // An absence is never a zero.
  serve(page(THREE), stats)

  render(<History />)

  await screen.findByText('Adaptive Sessions')
  expect(tile('Adaptive Accuracy')).toBe('—')
  if (!('total_questions' in stats)) expect(tile('Adaptive Questions')).toBe('—')
})

it('has no accuracy to show for no questions, rather than 0%', async () => {
  serve(page(THREE), { total_questions: 0, total_correct: 0, retrieved: true })

  render(<History />)

  await screen.findByText('Adaptive Sessions')
  expect(tile('Adaptive Questions')).toBe('0')
  expect(tile('Adaptive Accuracy')).toBe('—')
})

it('says nothing about questions or accuracy when the totals could not be read', async () => {
  serve(page(THREE), { retrieved: false })

  render(<History />)

  await screen.findByText('Adaptive Sessions')
  expect(tile('Adaptive Questions')).toBe('—')
  expect(tile('Adaptive Accuracy')).toBe('—')
})

it('says the list was cut, and still shows how many sessions there really are', async () => {
  serve({ sessions: THREE, total: 431, truncated: true })

  render(<History />)

  expect(await screen.findByText(/showing your 3 most recent adaptive sessions of 431/i))
    .toBeInTheDocument()
  expect(tile('Adaptive Sessions')).toBe('431')
})

it('says nothing about a whole history', async () => {
  // Teeth for the notice above.
  serve({ sessions: THREE, total: 3, truncated: false })

  render(<History />)

  await screen.findByText('Adaptive Sessions')
  expect(screen.queryByText(/most recent adaptive sessions/i)).not.toBeInTheDocument()
})

it('claims no count at all when the backend could not count', async () => {
  // `total: null`: the count did not come back, so the rows' length is not a total.
  serve({ sessions: THREE, total: null, truncated: null })

  render(<History />)

  await screen.findByText('Adaptive Sessions')
  expect(tile('Adaptive Sessions')).toBe('—')
  expect(screen.queryByText(/most recent adaptive sessions/i)).not.toBeInTheDocument()
})

// ── practice sessions are listed beside adaptive ones ────────────────────────

it('lists practice sessions with the adaptive ones, newest first, each labelled', async () => {
  const flashcards = { ...PRACTICE, id: 'p2', mode: 'flashcard', started_at: '2026-08-14T08:00:00Z',
                       questions_answered: 12, correct_answers: 0 }
  serve(page([SESSION]), STATS, [PRACTICE, flashcards])

  render(<History />)

  const titles = (await screen.findAllByText(/^(Practice · \w+|Adaptive Session)$/)).map(e => e.textContent)
  // 16 Aug test, 15 Aug adaptive, 14 Aug flashcards.
  expect(titles).toEqual(['Practice · Test', 'Adaptive Session', 'Practice · Flashcards'])
  expect(apiFetch).toHaveBeenCalledWith('/api/practice-sessions')
  expect(screen.getByText('80%')).toBeInTheDocument()          // the practice test, 8 of 10
  // Flashcards are ungraded: no accuracy, and the count is cards, never a 0%.
  expect(screen.getByText('not graded')).toBeInTheDocument()
  expect(screen.getByText('cards')).toBeInTheDocument()
  expect(screen.queryByText('0%')).not.toBeInTheDocument()
})

it('says the practice list failed rather than showing no practice, and keeps the lessons', async () => {
  serve(page([SESSION]))
  overrideApi('/api/practice-sessions', () => { throw apiError(500, 'down') })

  render(<History />)

  expect(await screen.findByText(/couldn't load your practice sessions/i)).toBeInTheDocument()
  expect(screen.getByText('Adaptive Session')).toBeInTheDocument()
})

it('retries the practice list from its own notice', async () => {
  serve(page([SESSION]))
  let calls = 0
  overrideApi('/api/practice-sessions', () => {
    calls += 1
    if (calls === 1) throw apiError(503)
    return [PRACTICE]
  })
  render(<History />)
  await screen.findByText(/couldn't load your practice sessions/i)

  await userEvent.click(screen.getByRole('button', { name: /try again/i }))
  expect(await screen.findByText('Practice · Test')).toBeInTheDocument()
  expect(screen.queryByText(/couldn't load your practice sessions/i)).not.toBeInTheDocument()
})

it('says a full practice page is the most recent ones, since the list has no count', async () => {
  const twenty = Array.from({ length: 20 }, (_, i) => ({ ...PRACTICE, id: `p${i}` }))
  serve(page([SESSION]), STATS, twenty)

  render(<History />)

  expect(await screen.findByText(/showing your 20 most recent practice sessions/i)).toBeInTheDocument()
})

it('shows a practice session left open as not finished, and keeps it out of In Progress', async () => {
  // The backend flags it (`abandoned`): no sweep closes a practice session.
  const left = { ...PRACTICE, id: 'p-left', ended_at: null, abandoned: true }
  const live = { ...PRACTICE, id: 'p-live', ended_at: null, abandoned: false,
                 started_at: '2026-08-17T09:00:00Z' }
  serve(page([]), STATS, [left, live])

  render(<History />)

  expect(await screen.findByText(/not finished/i)).toBeInTheDocument()
  await userEvent.click(screen.getByRole('button', { name: 'In Progress' }))
  expect(screen.getAllByText('Practice · Test')).toHaveLength(1)
  expect(screen.queryByText(/not finished/i)).not.toBeInTheDocument()
})

it('shows no accuracy for a session with no answers, never a red 0%', async () => {
  const empty = { ...PRACTICE, id: 'p-empty', ended_at: null, abandoned: true,
                  questions_answered: 0, correct_answers: 0 }
  serve(page([]), STATS, [empty])

  render(<History />)

  expect(await screen.findByText('Practice · Test')).toBeInTheDocument()
  expect(screen.queryByText('0%')).not.toBeInTheDocument()
  expect(screen.getByText('accuracy').previousElementSibling.textContent).toBe('—')
})
