import { render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { vi } from 'vitest'
import ClassDetail from './ClassDetail'

// Guards against crashes reachable from a real API response, plus the load-error state.

vi.mock('../../lib/api', () => ({ apiFetch: vi.fn() }))
vi.mock('sonner', () => ({ toast: { error: vi.fn(), success: vi.fn() } }))

const { apiFetch } = await import('../../lib/api')

const CLASS_ID = 'class-1'

function renderAt(id = CLASS_ID) {
  return render(
    <MemoryRouter initialEntries={[`/teacher/classes/${id}`]}>
      <Routes>
        <Route path="/teacher/classes/:id" element={<ClassDetail />} />
      </Routes>
    </MemoryRouter>,
  )
}

// Resolves by URL, not call order, so reordering the calls in loadData can't silently swap fixtures.
function mockLoad(cls, students) {
  apiFetch.mockReset()
  apiFetch.mockImplementation((url) =>
    Promise.resolve(String(url).includes('/students') ? students : cls),
  )
}

it('renders a class with students', async () => {
  mockLoad(
    { id: CLASS_ID, name: 'Algebra', join_code: 'ABC123', grade_level: '7' },
    [{ user_id: 's1', name: 'Ada' }],
  )
  renderAt()
  expect(await screen.findByText('Algebra')).toBeInTheDocument()
  expect(screen.getByText('Students (1)')).toBeInTheDocument()
})

it('survives the students endpoint returning a non-array', async () => {
  mockLoad({ id: CLASS_ID, name: 'Algebra', join_code: 'ABC123' }, null)
  renderAt()
  expect(await screen.findByText('Algebra')).toBeInTheDocument()
  expect(screen.getByText('Students (0)')).toBeInTheDocument()
})

it('survives a class with an empty name', async () => {
  mockLoad({ id: CLASS_ID, name: '', join_code: 'ABC123' }, [])
  renderAt()
  expect(await screen.findByText('Untitled class')).toBeInTheDocument()
})

it('distinguishes a failed request from a missing class', async () => {
  apiFetch.mockReset()
  apiFetch.mockRejectedValue(new Error('Not your class'))
  renderAt()
  expect(await screen.findByText(/couldn't load this class/i)).toBeInTheDocument()
  expect(screen.getByText('Not your class')).toBeInTheDocument()
  expect(screen.queryByText('Class not found.')).not.toBeInTheDocument()
})

const notFound = (msg) => Object.assign(new Error(msg), { status: 404 })

it('still reports a genuinely missing class as not found', async () => {
  // Both routes 404, matching what a missing class actually produces (the roster runs the same owner check).
  apiFetch.mockReset()
  apiFetch.mockRejectedValue(notFound('Class not found'))
  renderAt()
  expect(await screen.findByText('Class not found.')).toBeInTheDocument()
  expect(screen.queryByText(/couldn't load this class/i)).not.toBeInTheDocument()
})

it('does not blame the class for a 404 from the roster', async () => {
  // The 404 -> "not found" translation applies only to the class request, not the roster's.
  apiFetch.mockReset()
  apiFetch.mockImplementation((url) =>
    String(url).includes('/students')
      ? Promise.reject(notFound('Not Found'))
      : Promise.resolve({ id: CLASS_ID, name: 'Algebra', join_code: 'ABC123' }),
  )
  renderAt()
  expect(await screen.findByText(/couldn't load this class/i)).toBeInTheDocument()
  expect(screen.queryByText('Class not found.')).not.toBeInTheDocument()
})
