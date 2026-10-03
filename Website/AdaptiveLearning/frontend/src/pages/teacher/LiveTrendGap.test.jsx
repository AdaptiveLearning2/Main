/** A failed poll breaks the live trend: readings either side of an outage are not joined. */
import { it, expect, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'

vi.mock('../../lib/api', async () => await import('../../test/mocks/apiFetch'))
// jsdom sizes charts at 0x0, so the container is bypassed and LineChart reports its data.
const mockDrawn = []
vi.mock('recharts', async (importOriginal) => {
  const actual = await importOriginal()
  return {
    ...actual,
    ResponsiveContainer: ({ children }) => children,
    LineChart: (props) => { mockDrawn.push(props.data); return null },
  }
})

const { mockApi, apiError } = await import('../../test/mocks/apiFetch')
const { default: Live } = await import('./Live')

function row(ts, focus, stress) {
  return {
    user_id: 's1', name: 'Sam', email: 's@x.y', active_session: { id: 'sess-1' },
    latest_cognitive: { ts, focus, stress }, latest_face: null, latest_heart: null,
  }
}

it('puts a gap between the readings before and after a failed poll', async () => {
  let polls = 0
  mockApi({
    '/api/classes': () => [{ id: 'c1', name: 'Year 4' }],
    '/api/teacher/classes/c1/live': () => {
      polls += 1
      if (polls === 1) return [row('2026-10-02T09:00:00Z', 0.6, 0.3)]
      if (polls === 2) throw apiError(500, 'Internal Server Error')
      return [row('2026-10-02T09:00:10Z', 0.4, 0.2)]
    },
  })
  render(<MemoryRouter><Live /></MemoryRouter>)

  // 2 s to the failure, then a 4 s backoff to the recovery.
  await waitFor(() => expect(polls).toBeGreaterThanOrEqual(3), { timeout: 12_000 })
  await waitFor(() => {
    const drawn = mockDrawn.at(-1)
    expect(drawn.map(p => p.focus)).toEqual([0.6, null, 0.4])
    expect(drawn.map(p => p.stress)).toEqual([0.3, null, 0.2])
  })
  // The gap is not a reading.
  expect(screen.getByRole('img', { name: /last 2 readings/ })).toBeInTheDocument()
}, 20_000)
