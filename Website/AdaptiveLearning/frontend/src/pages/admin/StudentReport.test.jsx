/** The admin's own route to a student's report: the teacher route's RoleGuard sends an admin away. */
import { it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'

vi.mock('../../components/reports/StudentProgressReport', () => ({
  default: (props) => <div data-testid="report" data-props={JSON.stringify(props)} />,
}))

import AdminStudentReport from './StudentReport'

it('reports on the student in the URL, and leads back to the console', () => {
  render(
    <MemoryRouter initialEntries={[{ pathname: '/admin/students/u-1/report', state: { name: 'Ada' } }]}>
      <Routes>
        <Route path="/admin/students/:id/report" element={<AdminStudentReport />} />
      </Routes>
    </MemoryRouter>,
  )
  const props = JSON.parse(screen.getByTestId('report').dataset.props)
  expect(props).toMatchObject({ studentId: 'u-1', initialName: 'Ada', backTo: '/admin' })
})
