/**
 * The card that makes a link code, and its three states.
 *
 * A code is what a parent now needs to link to this account, so the block
 * replaced the one that showed the student's user id under "share this with a
 * parent to link accounts" -- an instruction that treated an id printed on
 * every roster payload as a shared secret.
 *
 * Only this card is exercised here; the rest of the page has no test file, and
 * a first one covering everything would be a different change.
 */
import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, waitFor, act } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

vi.mock('../../lib/api', async () => await import('../../test/mocks/apiFetch'))

const toastError = vi.fn()
const toastSuccess = vi.fn()
vi.mock('sonner', () => ({
  toast: { error: (...a) => toastError(...a), success: (...a) => toastSuccess(...a) },
}))

vi.mock('../../context/AuthContext', () => ({
  useAuth: () => ({
    user: { id: 'kid-1', email: 'ada@example.com', created_at: '2026-01-05T00:00:00Z' },
    displayName: 'Ada',
    refreshProfile: vi.fn(),
    signOut: vi.fn(),
  }),
}))

// Its own test file covers these; here they would only add fetches.
vi.mock('../../components/consent/ConsentChannels', () => ({
  default: () => <div data-testid="consent" />,
}))

import { apiFetch, mockApi, overrideApi, resetApi, apiError } from '../../test/mocks/apiFetch'
import Profile from './Profile'

const IN_TEN_MINUTES = () => new Date(Date.now() + 10 * 60_000).toISOString()

const happy = (linkCode) => ({
  '/api/stats/me': () => ({ total_questions: 10, total_correct: 5, retrieved: true }),
  '/api/sessions?limit=1': () => ({ sessions: [], total: 0, truncated: false }),
  '/api/profile/me': () => ({ display_name: 'Ada', grade_level: '5th Grade' }),
  '/api/student/link-code': () => linkCode,
  'POST /api/student/link-code': () => ({ code: 'WXYZ6789', expires_at: IN_TEN_MINUTES() }),
})

beforeEach(() => {
  resetApi()
  toastError.mockReset()
  toastSuccess.mockReset()
})

it('offers to create one when there is none', async () => {
  mockApi(happy({ code: null, expires_at: null, retrieved: true }))
  render(<Profile />)

  const button = await screen.findByRole('button', { name: /create a link code/i })

  await userEvent.click(button)

  expect(await screen.findByText('WXYZ6789')).toBeInTheDocument()
})

it('shows the outstanding code rather than making the student create another', async () => {
  // Each new code invalidates the last, so a card that always offered "create"
  // would hand out a replacement for a code already read out to a parent.
  mockApi(happy({ code: 'ABCD2345', expires_at: IN_TEN_MINUTES(), retrieved: true }))
  render(<Profile />)

  expect(await screen.findByText('ABCD2345')).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: /create a link code/i }))
    .not.toBeInTheDocument()
  // Replacing it is still offered, as a deliberate second step.
  expect(screen.getByRole('button', { name: /new code/i })).toBeInTheDocument()
})

it('says the read failed rather than offering to create one', async () => {
  // The third state, and the one that matters: "we could not check" rendered as
  // "you have none" puts a Create button in front of a student whose code is
  // live, and pressing it invalidates the code a parent is about to type.
  mockApi(happy({ code: null, expires_at: null, retrieved: false }))
  render(<Profile />)

  expect(await screen.findByText(/couldn't check for a code/i)).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: /create a link code/i }))
    .not.toBeInTheDocument()
})

it('treats a rejected request the same as a failed read', async () => {
  mockApi(happy({ code: null, expires_at: null, retrieved: true }))
  overrideApi('/api/student/link-code', () => { throw apiError(500, 'down') })
  render(<Profile />)

  expect(await screen.findByText(/couldn't check for a code/i)).toBeInTheDocument()
})

it('does not print the user id where the code goes', async () => {
  // The id is still on the account; what changed is that no surface presents it
  // as the thing to hand a parent.
  mockApi(happy({ code: 'ABCD2345', expires_at: IN_TEN_MINUTES(), retrieved: true }))
  render(<Profile />)

  await screen.findByText('ABCD2345')
  expect(screen.queryByText('kid-1')).not.toBeInTheDocument()
  expect(screen.queryByText(/share this with a parent/i)).not.toBeInTheDocument()
})

describe('a code on screen that stopped working', () => {
  // A parent redeemed it, or it expired. The card re-reads it rather than
  // going on offering a spent code as live until the page reloads.
  const live = { code: 'ABCD2345', expires_at: IN_TEN_MINUTES(), retrieved: true }
  const spent = () => overrideApi('/api/student/link-code',
    () => ({ code: null, expires_at: null, retrieved: true }))

  it('is dropped when the student comes back to the page', async () => {
    mockApi(happy(live))
    render(<Profile />)
    await screen.findByText('ABCD2345')

    spent()
    window.dispatchEvent(new Event('focus'))

    expect(await screen.findByRole('button', { name: /create a link code/i }))
      .toBeInTheDocument()
    expect(screen.queryByText('ABCD2345')).not.toBeInTheDocument()
  })

  it('is dropped while the student watches it, without any action', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    try {
      mockApi(happy(live))
      render(<Profile />)
      await screen.findByText('ABCD2345')

      spent()
      await vi.advanceTimersByTimeAsync(60_000)

      expect(await screen.findByRole('button', { name: /create a link code/i }))
        .toBeInTheDocument()
    } finally {
      vi.useRealTimers()
    }
  })

  it('is left standing by a re-read that failed', async () => {
    // "Could not check" is not "it was used".
    mockApi(happy(live))
    render(<Profile />)
    await screen.findByText('ABCD2345')

    overrideApi('/api/student/link-code',
      () => ({ code: null, expires_at: null, retrieved: false }))
    const reads = () => apiFetch.mock.calls.filter(([p, o]) =>
      p === '/api/student/link-code' && !o?.method).length
    const before = reads()
    window.dispatchEvent(new Event('focus'))
    await waitFor(() => expect(reads()).toBe(before + 1))
    // Let its answer land before looking.
    await act(async () => { await new Promise(r => setTimeout(r, 0)) })

    expect(screen.getByText('ABCD2345')).toBeInTheDocument()
  })
})

it('reports a refused creation instead of showing a code it did not get', async () => {
  mockApi(happy({ code: null, expires_at: null, retrieved: true }))
  overrideApi('/api/student/link-code',
              () => { throw apiError(503, 'Could not create a code just now.') },
              'POST')
  render(<Profile />)

  await userEvent.click(await screen.findByRole('button', { name: /create a link code/i }))

  await waitFor(() => expect(toastError).toHaveBeenCalled())
  expect(toastError.mock.calls[0][0]).toMatch(/could not create a code/i)
  expect(screen.getByRole('button', { name: /create a link code/i })).toBeInTheDocument()
})
