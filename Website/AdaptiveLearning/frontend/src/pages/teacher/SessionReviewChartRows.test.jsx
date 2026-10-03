/** The replay chart draws a downsampled series; the sentence describes the full one. */
import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, vi } from 'vitest'

vi.mock('../../lib/api', async () => await import('../../test/mocks/apiFetch'))
// jsdom sizes charts at 0x0, so the container is bypassed and LineChart reports its data.
const mockDrawn = []
vi.mock('recharts', async (importOriginal) => {
  const actual = await importOriginal()
  return {
    ...actual,
    ResponsiveContainer: ({ children }) => children,
    LineChart: (props) => { mockDrawn.push(props.data?.length ?? 0); return null },
  }
})

const { mockApi, resetApi } = await import('../../test/mocks/apiFetch')
const { default: SessionReview } = await import('./SessionReview')

beforeEach(() => resetApi())

it('draws at most the row budget of a long session, and describes every reading', async () => {
  const start = Date.parse('2026-08-10T09:00:00Z')
  const cognitive = Array.from({ length: 5000 }, (_, i) => ({
    ts: new Date(start + i * 250).toISOString(), focus: 0.5, stress: 0.3,
  }))
  // No archive route: a session with readings never asks for its archived charts.
  mockApi({ '/api/signals/session/s1': () => ({ cognitive, face: [], heart: [], answers: [] }) })
  render(
    <MemoryRouter initialEntries={['/teacher/sessions/s1/review']}>
      <Routes>
        <Route path="/teacher/sessions/:sessionId/review" element={<SessionReview />} />
      </Routes>
    </MemoryRouter>,
  )
  await waitFor(() => expect(screen.getByRole('img', { name: /over 5000 readings/ })).toBeInTheDocument())
  // The sr-only table samples the full series, not the drawn one.
  expect(screen.getByText(/rows sampled evenly across 5000\./)).toBeInTheDocument()
  expect(mockDrawn.length).toBeGreaterThan(0)
  expect(Math.max(...mockDrawn)).toBeLessThanOrEqual(1500)
})
