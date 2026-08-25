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

// ─── the last-active column ───────────────────────────────────────────────
//
// Three states, and the two that collapse most easily are the two that matter:
// a student who has genuinely never worked, and a read that failed. Reporting
// the second as the first tells a teacher the class has stopped working, which
// is both wrong and something they would act on.

it('shows how long ago a student was last active', async () => {
  const hourAgo = new Date(Date.now() - 60 * 60 * 1000).toISOString()
  mockLoad(
    { id: CLASS_ID, name: 'Algebra', join_code: 'ABC123' },
    [{ user_id: 's1', name: 'Ada', last_active: hourAgo, last_active_retrieved: true }],
  )
  renderAt()
  expect(await screen.findByText('Active 1h ago')).toBeInTheDocument()
})

it('a student who has never worked is not a failed read', async () => {
  mockLoad(
    { id: CLASS_ID, name: 'Algebra', join_code: 'ABC123' },
    [{ user_id: 's1', name: 'Ada', last_active: null, last_active_retrieved: true }],
  )
  renderAt()
  expect(await screen.findByText('Never active')).toBeInTheDocument()
})

it('a failed read says so rather than claiming the student is idle', async () => {
  mockLoad(
    { id: CLASS_ID, name: 'Algebra', join_code: 'ABC123' },
    [{ user_id: 's1', name: 'Ada', last_active: null, last_active_retrieved: false }],
  )
  renderAt()
  expect(await screen.findByText('Last active unknown')).toBeInTheDocument()
  expect(screen.queryByText('Never active')).not.toBeInTheDocument()
})

it('survives a roster from before the column existed', async () => {
  // An older payload carries neither key. Absent must not read as a failure,
  // and must not read as a timestamp either.
  mockLoad(
    { id: CLASS_ID, name: 'Algebra', join_code: 'ABC123' },
    [{ user_id: 's1', name: 'Ada' }],
  )
  renderAt()
  expect(await screen.findByText('Never active')).toBeInTheDocument()
})
