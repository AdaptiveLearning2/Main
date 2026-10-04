import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { vi } from 'vitest'
import ParentDashboard from './Dashboard'
import { mockApi, resetApi } from '../../test/mocks/apiFetch'

// The recent-sessions list: `null` is a failed read, `[]` is a child with none yet.

vi.mock('../../lib/api', async () => await import('../../test/mocks/apiFetch'))
vi.mock('../../context/AuthContext', () => ({
  useAuth: () => ({ user: { email: 'parent@example.com' }, displayName: 'Pat' }),
}))

function child(sessions) {
  return {
    user_id: 'kid-1', name: 'Ada', email: 'ada@example.com',
    stats: { total_questions: 10, total_correct: 6, current_streak: 2, retrieved: true },
    sessions,
    signal_summary: { focus: null, stress: null, sessions: 0, cognitive_samples: 0,
                      face_samples: 0, face_included: true, retrieved: true },
  }
}

function renderWith(sessions) {
  mockApi({
    '/api/parent/children': [child(sessions)],
    '/api/parent/consent-notices': { retrieved: true, notices: [] },
  })
  return render(<MemoryRouter><ParentDashboard /></MemoryRouter>)
}

beforeEach(() => {
  localStorage.clear()
  resetApi()
})

it('says the recent sessions could not be loaded when the read failed', async () => {
  renderWith(null)

  await screen.findByText('Ada')
  expect(screen.getByText("Recent sessions couldn't be loaded just now.")).toBeInTheDocument()
  expect(screen.queryByText('Recent Sessions')).not.toBeInTheDocument()
  // The other reads on the card are unaffected.
  expect(screen.getByText('Questions', { selector: 'p' }).previousElementSibling).toHaveTextContent('10')
})

it('claims no failure for a child with no sessions yet', async () => {
  renderWith([])

  await screen.findByText('Ada')
  expect(screen.queryByText(/recent sessions couldn't be loaded/i)).not.toBeInTheDocument()
  expect(screen.queryByText('Recent Sessions')).not.toBeInTheDocument()
})

it('lists the sessions that were read', async () => {
  renderWith([{ id: 's-1', title: 'Fractions', started_at: '2026-09-01T09:00:00Z',
                questions_answered: 4, correct_answers: 3 }])

  await screen.findByText('Ada')
  expect(screen.getByText('Fractions')).toBeInTheDocument()
  expect(screen.getByText('Recent Sessions')).toBeInTheDocument()
  expect(screen.queryByText(/recent sessions couldn't be loaded/i)).not.toBeInTheDocument()
})
