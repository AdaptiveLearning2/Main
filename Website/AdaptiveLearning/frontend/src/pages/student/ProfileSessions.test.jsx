/** Profile's Total Sessions tile reads the backend's `total`, never the capped list's length. */
import { it, expect, beforeEach, vi } from 'vitest'
import { render, screen } from '@testing-library/react'

vi.mock('../../lib/api', async () => await import('../../test/mocks/apiFetch'))

vi.mock('../../context/AuthContext', () => ({
  useAuth: () => ({
    user: { id: 'kid-1', email: 'ada@example.com', created_at: '2026-01-05T00:00:00Z' },
    displayName: 'Ada',
    refreshProfile: vi.fn(),
    signOut: vi.fn(),
  }),
}))

vi.mock('../../components/consent/ConsentChannels', () => ({ default: () => null }))

import { apiFetch, mockApi, overrideApi, resetApi, apiError } from '../../test/mocks/apiFetch'
import Profile from './Profile'

// Profile needs only the count, so it asks for one row; the router matches the query string.
const ONE = '/api/sessions?limit=1'
const ROW = { id: 's1', started_at: '2026-09-22T10:00:00Z', questions_answered: 3, correct_answers: 2 }

const tile = label => screen.getByText(label).previousElementSibling.textContent

const serve = sessions => mockApi({
  '/api/stats/me': () => ({ total_questions: 10, total_correct: 5, best_streak: 2, retrieved: true }),
  [ONE]: sessions,
  '/api/profile/me': () => ({ display_name: 'Ada' }),
  '/api/student/link-code': () => ({ code: null, expires_at: null, retrieved: true }),
})

beforeEach(() => { resetApi() })

it('shows how many sessions there are, not how many rows came back', async () => {
  serve(() => ({ sessions: [ROW], total: 431, truncated: true }))
  render(<Profile />)

  await screen.findByText('431')
  expect(tile('Total Sessions')).toBe('431')
  expect(apiFetch).toHaveBeenCalledWith(ONE)
})

it.each([
  ['the backend could not count', () => ({ sessions: [ROW], total: null, truncated: null })],
  ['the read failed', () => { throw apiError(500, 'down') }],
  ['the body is not one it reads', () => [ROW]],
])('claims no count when %s', async (_name, handler) => {
  serve(handler)
  render(<Profile />)

  // Once a stats tile shows a figure, the session read has settled too.
  await screen.findByText('10')
  expect(tile('Total Sessions')).toBe('—')
})

it('shows a real zero as zero', async () => {
  // The mirror, so the dash cannot pass by being the only thing ever drawn.
  serve(() => ({ sessions: [], total: 0, truncated: false }))
  overrideApi('/api/stats/me', () => ({ total_questions: 0, total_correct: 0, best_streak: 0, retrieved: true }))
  render(<Profile />)

  await screen.findByText('Total Sessions')
  await screen.findAllByText('0')
  expect(tile('Total Sessions')).toBe('0')
})
