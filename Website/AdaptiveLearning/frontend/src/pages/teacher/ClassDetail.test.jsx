import { fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes, useNavigate } from 'react-router-dom'
import { vi } from 'vitest'
import ClassDetail from './ClassDetail'

// Guards against crashes reachable from a real API response, plus the load-error state.

vi.mock('../../lib/api', () => ({ apiFetch: vi.fn() }))
vi.mock('sonner', () => ({ toast: { error: vi.fn(), success: vi.fn() } }))

const { apiFetch } = await import('../../lib/api')
const { toast } = await import('sonner')

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
  // As apiFetch throws for a non-2xx: the backend's detail as the message, the status attached.
  apiFetch.mockRejectedValue(Object.assign(new Error('Internal Server Error'), { status: 500 }))
  renderAt()
  // The whole sentence: "this class's students" also contains "this class".
  expect(await screen.findByText("Couldn't load this class. Make sure the backend is running.")).toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Try again' })).toBeInTheDocument()
  // The backend's detail string is not shown to a teacher.
  expect(screen.queryByText('Internal Server Error')).not.toBeInTheDocument()
  expect(screen.queryByText("Couldn't find this class.")).not.toBeInTheDocument()
})

it('says a refused class is refused, once, with no retry', async () => {
  apiFetch.mockReset()
  toast.error.mockClear()
  apiFetch.mockRejectedValue(Object.assign(new Error('Not your class'), { status: 403 }))
  renderAt()
  expect(await screen.findByText("You don't have access to this class.")).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: 'Try again' })).not.toBeInTheDocument()
  expect(screen.queryByText('Not your class')).not.toBeInTheDocument()
  expect(toast.error).not.toHaveBeenCalled()
})

const notFound = (msg) => Object.assign(new Error(msg), { status: 404 })

it('still reports a genuinely missing class as not found', async () => {
  // Both routes 404, as a missing class does (same owner check).
  apiFetch.mockReset()
  apiFetch.mockRejectedValue(notFound('Class not found'))
  renderAt()
  expect(await screen.findByText("Couldn't find this class.")).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: 'Try again' })).not.toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Back to Classes' })).toBeInTheDocument()
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
  // The class itself loaded, so the sentence names the roster rather than the class.
  expect(await screen.findByText("Couldn't find this class's students.")).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: 'Try again' })).not.toBeInTheDocument()
  expect(screen.queryByText("Couldn't find this class.")).not.toBeInTheDocument()
})

it('offers Try again when the roster read failed just now, and recovers on it', async () => {
  // A failed read behind the owner check is a 503, never a 404 claiming the roster is gone.
  apiFetch.mockReset()
  let rosterUp = false
  apiFetch.mockImplementation((url) =>
    !String(url).includes('/students')
      ? Promise.resolve({ id: CLASS_ID, name: 'Algebra', join_code: 'ABC123' })
      : rosterUp
        ? Promise.resolve([{ user_id: 's1', name: 'Ada' }])
        : Promise.reject(Object.assign(new Error('Class could not be loaded; try again'), { status: 503 })),
  )
  renderAt()
  expect(await screen.findByText("Couldn't load this class's students just now. Try again in a moment."))
    .toBeInTheDocument()
  rosterUp = true
  fireEvent.click(screen.getByRole('button', { name: 'Try again' }))
  expect(await screen.findByText('Students (1)')).toBeInTheDocument()
})

it("starts another class from fresh state, not the last class's error", async () => {
  // Same route, new id: the loaders reset nothing themselves, so the page must remount.
  apiFetch.mockReset()
  apiFetch.mockImplementation((url) =>
    String(url).includes('/class-gone') ? Promise.reject(notFound('Class not found')) : new Promise(() => {}))
  function GoTo() {
    const navigate = useNavigate()
    return <button onClick={() => navigate('/teacher/classes/class-2')}>next class</button>
  }
  render(
    <MemoryRouter initialEntries={['/teacher/classes/class-gone']}>
      <Routes>
        <Route path="/teacher/classes/:id" element={<><ClassDetail /><GoTo /></>} />
      </Routes>
    </MemoryRouter>,
  )
  expect(await screen.findByText("Couldn't find this class.")).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'next class' }))
  expect(screen.queryByText("Couldn't find this class.")).not.toBeInTheDocument()
})

// ─── the last-active column ───────────────────────────────────────────────
// Three states: a timestamp, never active, and a failed read.

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
  // An older payload carries neither key: neither a failure nor a timestamp.
  mockLoad(
    { id: CLASS_ID, name: 'Algebra', join_code: 'ABC123' },
    [{ user_id: 's1', name: 'Ada' }],
  )
  renderAt()
  expect(await screen.findByText('Never active')).toBeInTheDocument()
})
