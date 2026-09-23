import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { vi } from 'vitest'
import History from './History'

// A failed request must not look like an empty list -- "No sessions here" would
// be a false claim about a backend that's just unreachable. History stands in
// for other list pages sharing this shape; they all use the same LoadError
// component.

vi.mock('../../lib/api', () => ({ apiFetch: vi.fn() }))
const { apiFetch } = await import('../../lib/api')

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

beforeEach(() => { apiFetch.mockReset() })

it('says the read failed rather than claiming there are no sessions', async () => {
  apiFetch.mockRejectedValue(new Error('backend down'))

  render(<History />)

  expect(await screen.findByText(/couldn't load your session history/i)).toBeInTheDocument()
  expect(screen.queryByText(/no sessions here/i)).not.toBeInTheDocument()
})

it('still reports a genuinely empty history as empty', async () => {
  // A new student really has no sessions -- this must not be treated as a failure either.
  apiFetch.mockResolvedValue(page([]))

  render(<History />)

  expect(await screen.findByText(/no sessions here/i)).toBeInTheDocument()
  expect(screen.queryByText(/couldn't load/i)).not.toBeInTheDocument()
})

it('retries without a page reload', async () => {
  apiFetch.mockRejectedValueOnce(new Error('transient'))
  render(<History />)
  await screen.findByText(/couldn't load your session history/i)

  apiFetch.mockResolvedValue(page([SESSION]))
  await userEvent.click(screen.getByRole('button', { name: /try again/i }))

  await waitFor(() =>
    expect(screen.queryByText(/couldn't load/i)).not.toBeInTheDocument())
  expect(apiFetch).toHaveBeenCalledTimes(2)
})

it('does not show the summary tiles when the read failed', async () => {
  // The tiles are derived from `sessions`, so an empty array would render a
  // confident "0 questions, 0% accuracy" beside the error.
  apiFetch.mockRejectedValue(new Error('backend down'))

  render(<History />)

  await screen.findByText(/couldn't load/i)
  expect(screen.queryByText('Questions Done')).not.toBeInTheDocument()
  expect(screen.queryByText('Overall Accuracy')).not.toBeInTheDocument()
})

// ── the cap, and saying so ────────────────────────────────────────────────
//
// The backend caps this read. The page counts the rows, sums their questions
// and divides for an accuracy, so a cut list that said nothing would not
// render as a shortened view -- it would render as a student who did less
// work. These three are the three states the payload can carry.

const CUT = [
  { ...SESSION, id: 's1', questions_answered: 6, correct_answers: 4 },
  { ...SESSION, id: 's2', questions_answered: 4, correct_answers: 2 },
]

// The figure above a tile's label. Scoped, because the same small numbers
// appear on every session row below -- a bare `getByText('2')` matches the
// row's "correct" count as readily as the tile's.
const tile = label =>
  screen.getByText(label).previousElementSibling.textContent

it('says the list was cut, and still shows how many sessions there really are', async () => {
  apiFetch.mockResolvedValue({ sessions: CUT, total: 431, truncated: true })

  render(<History />)

  expect(await screen.findByText(/showing your 2 most recent sessions of 431/i))
    .toBeInTheDocument()
  // The tile reports the real count, not the length of what was sent.
  expect(tile('Total Sessions')).toBe('431')
})

it('says nothing about a whole history', async () => {
  // Or the notice above is satisfied by a page that always draws it.
  apiFetch.mockResolvedValue({ sessions: CUT, total: 2, truncated: false })

  render(<History />)

  await screen.findByText('Total Sessions')
  expect(screen.queryByText(/most recent sessions/i)).not.toBeInTheDocument()
})

it('says nothing when the backend could not tell either', async () => {
  // `truncated: null` is the third state -- the count did not come back. A
  // notice here would claim the list was cut on the strength of a number
  // nobody received, and its absence must not claim the opposite.
  apiFetch.mockResolvedValue({ sessions: CUT, total: null, truncated: null })

  render(<History />)

  await screen.findByText('Total Sessions')
  expect(screen.queryByText(/most recent sessions/i)).not.toBeInTheDocument()
  // Falls back to what it was sent rather than rendering nothing.
  expect(tile('Total Sessions')).toBe('2')
})
