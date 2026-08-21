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

beforeEach(() => { apiFetch.mockReset() })

it('says the read failed rather than claiming there are no sessions', async () => {
  apiFetch.mockRejectedValue(new Error('backend down'))

  render(<History />)

  expect(await screen.findByText(/couldn't load your session history/i)).toBeInTheDocument()
  expect(screen.queryByText(/no sessions here/i)).not.toBeInTheDocument()
})

it('still reports a genuinely empty history as empty', async () => {
  // A new student really has no sessions -- this must not be treated as a failure either.
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
