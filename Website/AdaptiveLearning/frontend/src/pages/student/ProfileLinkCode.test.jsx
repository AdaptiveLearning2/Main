/** The link-code card and its three states; a user id is not a secret to share. */
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
  // Each new code invalidates the last.
  mockApi(happy({ code: 'ABCD2345', expires_at: IN_TEN_MINUTES(), retrieved: true }))
  render(<Profile />)

  expect(await screen.findByText('ABCD2345')).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: /create a link code/i }))
    .not.toBeInTheDocument()
  // Replacing it is still offered, as a deliberate second step.
  expect(screen.getByRole('button', { name: /new code/i })).toBeInTheDocument()
})

it('says the read failed rather than offering to create one', async () => {
  // "Could not check" as "none" offers Create, which would invalidate a live code.
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
  // No surface presents the id as the thing to hand a parent.
  mockApi(happy({ code: 'ABCD2345', expires_at: IN_TEN_MINUTES(), retrieved: true }))
  render(<Profile />)

  await screen.findByText('ABCD2345')
  expect(screen.queryByText('kid-1')).not.toBeInTheDocument()
  expect(screen.queryByText(/share this with a parent/i)).not.toBeInTheDocument()
})

describe('a code on screen that stopped working', () => {
  // Redeemed or expired: the card re-reads rather than offering a spent code.
  const live = { code: 'ABCD2345', expires_at: IN_TEN_MINUTES(), retrieved: true }
  const spent = () => overrideApi('/api/student/link-code',
    () => ({ code: null, expires_at: null, retrieved: true }))
  const rereads = () => apiFetch.mock.calls.flatMap(([p, o], i) =>
    p === '/api/student/link-code' && !o?.method ? [apiFetch.mock.results[i]] : [])

  // The focus listener attaches in an effect after the code renders, so one early event can be
  // lost; focus until a re-read goes out, and return every re-read since `before`.
  async function focusUntilReread(before) {
    await waitFor(() => {
      window.dispatchEvent(new Event('focus'))
      expect(rereads().length).toBeGreaterThan(before)
    })
    return rereads().slice(before)
  }

  it('is dropped when the student comes back to the page', async () => {
    mockApi(happy(live))
    render(<Profile />)
    await screen.findByText('ABCD2345')

    spent()
    // Focus until the page has re-read and dropped it, not merely until some read went out.
    await waitFor(() => {
      window.dispatchEvent(new Event('focus'))
      expect(screen.getByRole('button', { name: /create a link code/i })).toBeInTheDocument()
    })
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
    const sent = await focusUntilReread(rereads().length)
    // Let every answer land before looking.
    await act(async () => { await Promise.allSettled(sent.map(r => r.value)) })

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
