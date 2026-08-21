import { render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { vi } from 'vitest'
import StudentReport from './StudentReport'

// Tests the "back" link and heading built from router state, including the
// direct-visit case where no state exists (refresh / bookmark / deep link).

vi.mock('../../lib/api', () => ({ apiFetch: vi.fn() }))

const { apiFetch } = await import('../../lib/api')

const SID = 'stu-1'

beforeEach(() => {
  apiFetch.mockReset()
  // Resolve by URL, not call order, so fixtures can't get silently swapped.
  apiFetch.mockImplementation((url) => {
    const u = String(url)
    if (u.includes('/stats/'))        return Promise.resolve({ total_questions: 0, total_correct: 0, current_streak: 0 })
    if (u.includes('/weekly-report')) return Promise.resolve(null)
    return Promise.resolve([]) // sessions and performance
  })
})

function renderWithState(state) {
  return render(
    <MemoryRouter initialEntries={[{ pathname: `/teacher/students/${SID}/report`, state }]}>
      <Routes>
        <Route path="/teacher/students/:id/report" element={<StudentReport />} />
      </Routes>
    </MemoryRouter>,
  )
}

it('links back to the specific class using name and id from router state', async () => {
  renderWithState({ name: 'Ada', classId: 'class-1', className: 'Algebra' })
  await screen.findByText('Recent Sessions') // wait for fetches to settle
  const back = screen.getByRole('link', { name: /back to algebra/i })
  expect(back).toHaveAttribute('href', '/teacher/classes/class-1')
})

it('seeds the heading from the name in router state', async () => {
  renderWithState({ name: 'Ada', classId: 'class-1', className: 'Algebra' })
  expect(await screen.findByText("Ada's Progress")).toBeInTheDocument()
})

it('falls back to a generic class label when only the id is known', async () => {
  renderWithState({ classId: 'class-1' })
  await screen.findByText('Recent Sessions')
  const back = screen.getByRole('link', { name: 'Back to Class' })
  expect(back).toHaveAttribute('href', '/teacher/classes/class-1')
})

it('falls back to the class list on a direct visit with no state', async () => {
  renderWithState(undefined)
  await screen.findByText('Recent Sessions')
  const back = screen.getByRole('link', { name: 'Back to Classes' })
  expect(back).toHaveAttribute('href', '/teacher/classes')
  // No name anywhere, so the placeholder heading stays.
  expect(screen.getByText("Student's Progress")).toBeInTheDocument()
})

it('shows an error state, not an empty report, when the core load fails', async () => {
  // A failed load must show an error, not a zeros-filled report that looks
  // like a real but inactive student.
  apiFetch.mockReset()
  apiFetch.mockImplementation((url) =>
    String(url).includes('/weekly-report')
      ? Promise.resolve(null)
      : Promise.reject(new Error('You do not have access to this student')),
  )
  renderWithState({ name: 'Ada', classId: 'class-1', className: 'Algebra' })

  expect(await screen.findByText(/couldn't load this student's report/i)).toBeInTheDocument()
  expect(screen.getByText('You do not have access to this student')).toBeInTheDocument()
  // Report body must not render...
  expect(screen.queryByText('Recent Sessions')).not.toBeInTheDocument()
  // ...but the back link stays so the teacher can still leave.
  expect(screen.getByRole('link', { name: /back to algebra/i })).toHaveAttribute('href', '/teacher/classes/class-1')
})
