import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { vi } from 'vitest'
import StudentReport from './StudentReport'
import { clearViewPrefs } from '../../lib/viewPrefs'

// Tests the "back" link and heading built from router state, including the
// direct-visit case where no state exists (refresh / bookmark / deep link).

vi.mock('../../lib/api', () => ({ apiFetch: vi.fn() }))

const { apiFetch } = await import('../../lib/api')

const SID = 'stu-1'

beforeEach(() => {
  // This page owns a *persisted* preference: the sensor switch writes to
  // localStorage, which jsdom keeps for the whole file. Without this, every
  // test declared after one that flips the switch renders with sensors
  // already hidden -- silently, and only for the tests written later, so it
  // reads as one of them being broken rather than as leaked state.
  clearViewPrefs()
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

/**
 * The strategies panel was parent-only, on the reasoning that its advice is
 * written for someone at home. But the endpoint behind it is gated on
 * relationship rather than role -- its own docstring says so -- so a teacher
 * of this student could always ask for the advice and had no way to see it.
 *
 * On demand, not on mount: the panel fetches nothing until the button is
 * pressed, which is what keeps it from spending a model call per report page
 * across a class of thirty.
 */
it('offers the strategies panel, framed for a teacher and generating nothing on its own', async () => {
  renderWithState({ name: 'Ada', classId: 'class-1', className: 'Algebra' })
  await screen.findByText('Recent Sessions')

  expect(screen.getByRole('button', { name: /generate strategies/i })).toBeInTheDocument()
  expect(screen.getByText(/written for a family to use at home/i)).toBeInTheDocument()
  expect(apiFetch.mock.calls.some(([u]) => String(u).includes('/learning-strategies'))).toBe(false)
})

/**
 * The strategies panel goes behind "Hide sensor data" with the charts, because
 * the advice *is* sensor data in prose: the rule-based list says "stress
 * indicators ran high this week" and "focus indicators were low this week",
 * and the model pass is handed the same averages. Unconditional, the switch
 * took the tiles off screen and left a button that writes those numbers back
 * out as sentences.
 *
 * The button's absence is the assertion, not the panel's heading -- hiding the
 * heading while leaving a live Generate button would satisfy a heading check
 * and none of the point.
 */
it('hides the strategies panel behind the sensor switch, button included', async () => {
  renderWithState({ name: 'Ada', classId: 'class-1', className: 'Algebra' })
  await screen.findByText('Recent Sessions')
  expect(screen.getByRole('button', { name: /generate strategies/i })).toBeInTheDocument()

  await userEvent.click(screen.getByRole('switch', { name: /hide sensor data/i }))

  expect(screen.queryByRole('button', { name: /generate strategies/i })).not.toBeInTheDocument()
  expect(screen.queryByText(/at-home learning strategies/i)).not.toBeInTheDocument()
  // Academic content is untouched -- the switch hides sensor data, and these
  // measure answers.
  expect(screen.getByText('Recent Sessions')).toBeInTheDocument()
})

/**
 * Declared after the test that flips the switch, and that position is the
 * whole point: the preference is persisted, so without the `clearViewPrefs()`
 * in `beforeEach` this renders with sensors already hidden and fails. A guard
 * against leaked state is only a guard if something is standing downstream of
 * the leak.
 */
it('starts each test showing sensor data, whatever an earlier test switched off', async () => {
  renderWithState({ name: 'Ada', classId: 'class-1', className: 'Algebra' })
  await screen.findByText('Recent Sessions')

  expect(screen.getByRole('switch', { name: /hide sensor data/i })).toHaveAttribute('aria-checked', 'false')
  expect(screen.getByRole('button', { name: /generate strategies/i })).toBeInTheDocument()
})
