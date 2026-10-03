/** The class-average tiles: an unread average is a dash with a reason, never a measured 0. */
import { it, expect, beforeEach, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'

vi.mock('../../lib/api', async () => await import('../../test/mocks/apiFetch'))
vi.mock('sonner', () => ({ toast: { error: vi.fn(), success: vi.fn() } }))
vi.mock('../../context/AuthContext', () => ({ useAuth: () => ({ displayName: 'Ms Rao' }) }))
// Records what the page warms; a real chunk load mid-test would outlive the test.
const mockPrefetched = []
vi.mock('../../lib/prefetch', () => ({ usePrefetchWhenIdle: loader => { mockPrefetched.push(loader) } }))

import { mockApi, resetApi } from '../../test/mocks/apiFetch'
import TeacherDashboard from './Dashboard'
import { pages } from '../../routes'

const CLASS = { id: 'c1', name: 'Maths 5', join_code: 'ABC123', class_memberships: [{ count: 3 }] }

function renderWith(summary) {
  mockApi({
    '/api/questions?limit=5': () => [],
    '/api/questions/count': () => ({ total: 0, retrieved: true }),
    '/api/classes': () => [CLASS],
    '/api/classes/summary': () => summary,
  })
  render(<MemoryRouter><TeacherDashboard /></MemoryRouter>)
}

function streakTile() {
  return screen.getByText('Avg streak').previousElementSibling
}

beforeEach(() => { resetApi() })

it('warms the class page, the usual next click, once it has rendered', () => {
  renderWith({})
  expect(mockPrefetched).toContain(pages.classDetail)
})

it('shows a failed summary read as unread, not as a streak of 0', async () => {
  renderWith({ c1: { avgAccuracy: null, avgStreak: null, retrieved: false } })

  expect(await screen.findByText('These figures could not be loaded.')).toBeInTheDocument()
  expect(streakTile()).toHaveTextContent('—')
})

it('asks for the class averages without waiting for the class list', async () => {
  // Independent reads: chained, the summary waited one whole round trip for nothing.
  let summaryAsked = false
  mockApi({
    '/api/questions?limit=5': () => [],
    '/api/questions/count': () => ({ total: 0, retrieved: true }),
    '/api/classes': () => new Promise(() => {}),
    '/api/classes/summary': () => { summaryAsked = true; return {} },
  })
  render(<MemoryRouter><TeacherDashboard /></MemoryRouter>)
  await waitFor(() => expect(summaryAsked).toBe(true))
})

it('shows a real average streak as a number', async () => {
  renderWith({ c1: { avgAccuracy: 60, avgStreak: 2, retrieved: true } })

  expect(await screen.findByText('60%')).toBeInTheDocument()
  expect(streakTile()).toHaveTextContent('2')
  expect(screen.queryByText('These figures could not be loaded.')).not.toBeInTheDocument()
})
