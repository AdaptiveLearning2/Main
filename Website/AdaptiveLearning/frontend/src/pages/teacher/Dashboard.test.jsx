/** The class-average tiles: an unread average is a dash with a reason, never a measured 0. */
import { it, expect, beforeEach, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'

vi.mock('../../lib/api', async () => await import('../../test/mocks/apiFetch'))
vi.mock('sonner', () => ({ toast: { error: vi.fn(), success: vi.fn() } }))
vi.mock('../../context/AuthContext', () => ({ useAuth: () => ({ displayName: 'Ms Rao' }) }))

import { mockApi, resetApi } from '../../test/mocks/apiFetch'
import TeacherDashboard from './Dashboard'

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

it('shows a failed summary read as unread, not as a streak of 0', async () => {
  renderWith({ c1: { avgAccuracy: null, avgStreak: null, retrieved: false } })

  expect(await screen.findByText('These figures could not be loaded.')).toBeInTheDocument()
  expect(streakTile()).toHaveTextContent('—')
})

it('shows a real average streak as a number', async () => {
  renderWith({ c1: { avgAccuracy: 60, avgStreak: 2, retrieved: true } })

  expect(await screen.findByText('60%')).toBeInTheDocument()
  expect(streakTile()).toHaveTextContent('2')
  expect(screen.queryByText('These figures could not be loaded.')).not.toBeInTheDocument()
})
