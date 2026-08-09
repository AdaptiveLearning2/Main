import { render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { vi } from 'vitest'
import ClassDetail from './ClassDetail'

// Regression guards for the crashes fixed in PR #16, plus the load-error state
// added alongside them. All three were reachable from a real API response.

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

// Resolves by URL rather than by call order. mockResolvedValueOnce chains bind
// to the sequence the two calls happen to be made in, so reordering them inside
// loadData would silently swap the fixtures instead of failing.
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
  // students was initialised to null and the render path calls .length on it
  // unguarded, so anything non-array took the whole page down.
  mockLoad({ id: CLASS_ID, name: 'Algebra', join_code: 'ABC123' }, null)
  renderAt()
  expect(await screen.findByText('Algebra')).toBeInTheDocument()
  expect(screen.getByText('Students (0)')).toBeInTheDocument()
})

it('survives a class with an empty name', async () => {
  // cls.name[0].toUpperCase() threw on "" because ""[0] is undefined.
  mockLoad({ id: CLASS_ID, name: '', join_code: 'ABC123' }, [])
  renderAt()
  expect(await screen.findByText('Untitled class')).toBeInTheDocument()
})

it('distinguishes a failed request from a missing class', async () => {
  // Both left cls null, so a 403 or an offline client told the teacher their
  // class did not exist -- sending them to look for the wrong problem.
  apiFetch.mockReset()
  apiFetch.mockRejectedValue(new Error('Not your class'))
  renderAt()
  expect(await screen.findByText(/couldn't load this class/i)).toBeInTheDocument()
  expect(screen.getByText('Not your class')).toBeInTheDocument()
  expect(screen.queryByText('Class not found.')).not.toBeInTheDocument()
})

it('still reports a genuinely missing class as not found', async () => {
  // The detail endpoint 404s on an id that isn't there; that is the one status
  // that means "no such class" rather than "the request failed".
  apiFetch.mockReset()
  const missing = Object.assign(new Error('Class not found'), { status: 404 })
  apiFetch.mockRejectedValue(missing)
  renderAt()
  expect(await screen.findByText('Class not found.')).toBeInTheDocument()
})
