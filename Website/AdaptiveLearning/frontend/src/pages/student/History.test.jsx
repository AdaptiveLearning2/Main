import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { vi } from 'vitest'
import History from './History'

// Every list page had the same two-state shape: skeleton, then rows or an
// empty state. A failed request set `loading` false and left the rows empty, so
// the page said "No sessions here" for a backend that was unreachable -- an
// absence asserted from data that never came back, which is the failure the
// reporting helpers carry `retrieved` to prevent, arriving through the page
// instead of the payload.
//
// History stands in for the eight pages that shared the shape (Achievements,
// JoinClass, Classes, Questions, Sessions, Analytics, Profile, and this one).
// They all route through the same LoadError component.

vi.mock('../../lib/api', () => ({ apiFetch: vi.fn() }))
const { apiFetch } = await import('../../lib/api')

const SESSION = {
  id: 's1', started_at: '2026-08-15T10:00:00Z', ended_at: '2026-08-15T10:30:00Z',
  questions_answered: 6, correct_answers: 4,
}

beforeEach(() => { apiFetch.mockReset() })

it('says the read failed rather than claiming there are no sessions', async () => {
  apiFetch.mockRejectedValue(new Error('backend down'))

  render(<History />)

  expect(await screen.findByText(/couldn't load your session history/i)).toBeInTheDocument()
  // The exact string the bug produced. A student who had practised all term
  // was told they never had.
  expect(screen.queryByText(/no sessions here/i)).not.toBeInTheDocument()
})

it('still reports a genuinely empty history as empty', async () => {
  // The mirror, so the failure state cannot be satisfied by treating every
  // empty list as broken -- a new student really has no sessions.
  apiFetch.mockResolvedValue([])

  render(<History />)

  expect(await screen.findByText(/no sessions here/i)).toBeInTheDocument()
  expect(screen.queryByText(/couldn't load/i)).not.toBeInTheDocument()
})

it('retries without a page reload', async () => {
  apiFetch.mockRejectedValueOnce(new Error('transient'))
  render(<History />)
  await screen.findByText(/couldn't load your session history/i)

  apiFetch.mockResolvedValue([SESSION])
  await userEvent.click(screen.getByRole('button', { name: /try again/i }))

  // The error clears and the session appears, from one click -- no reload.
  await waitFor(() =>
    expect(screen.queryByText(/couldn't load/i)).not.toBeInTheDocument())
  expect(apiFetch).toHaveBeenCalledTimes(2)
})

it('does not show the summary tiles when the read failed', async () => {
  // They are derived by reducing over `sessions`, so an empty array renders a
  // confident "0 questions, 0% accuracy" beside the error.
  apiFetch.mockRejectedValue(new Error('backend down'))

  render(<History />)

  await screen.findByText(/couldn't load/i)
  expect(screen.queryByText('Questions Done')).not.toBeInTheDocument()
  expect(screen.queryByText('Overall Accuracy')).not.toBeInTheDocument()
})
