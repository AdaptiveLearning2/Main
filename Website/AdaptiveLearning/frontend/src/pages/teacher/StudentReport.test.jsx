import { render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { vi } from 'vitest'
import StudentReport from './StudentReport'

// The teacher report derives its "back" link from the router state the class
// roster passes, and seeds the heading from that same state so the name shows
// before the weekly-report loads. These pin both, including the direct-visit
// fallback where no state was passed (refresh / bookmark / deep link).

vi.mock('../../lib/api', () => ({ apiFetch: vi.fn() }))

const { apiFetch } = await import('../../lib/api')

const SID = 'stu-1'

beforeEach(() => {
  apiFetch.mockReset()
  // Resolve by URL rather than call order so reordering the fetches inside the
  // report can't silently swap fixtures. weekly-report is null here so the name
  // stays whatever router state seeded -- that is what these assert.
  apiFetch.mockImplementation((url) => {
    const u = String(url)
    if (u.includes('/stats/'))        return Promise.resolve({ total_questions: 0, total_correct: 0, current_streak: 0 })
    if (u.includes('/weekly-report')) return Promise.resolve(null)
    return Promise.resolve([]) // sessions + performance
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
  await screen.findByText('Recent Sessions') // let the initial fetches settle
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
  // No name in state, and weekly-report supplied none, so the placeholder holds.
  expect(screen.getByText("Student's Progress")).toBeInTheDocument()
})
