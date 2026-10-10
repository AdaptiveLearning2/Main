/** A failed poll breaks the live trend: readings either side of an outage are not joined. */
import { Children } from 'react'
import { it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'

vi.mock('../../lib/api', async () => await import('../../test/mocks/apiFetch'))
// jsdom sizes charts at 0x0, so the container is bypassed and LineChart reports its data and lines.
const mockDrawn = []
vi.mock('recharts', async (importOriginal) => {
  const actual = await importOriginal()
  return {
    ...actual,
    ResponsiveContainer: ({ children }) => children,
    LineChart: (props) => {
      const lines = Children.toArray(props.children).filter(c => c.props?.dataKey).map(c => c.props)
      mockDrawn.push({ data: props.data, lines })
      return null
    },
  }
})

const { mockApi, resetApi, apiError } = await import('../../test/mocks/apiFetch')
const { default: Live } = await import('./Live')

beforeEach(() => {
  resetApi()
  mockDrawn.length = 0
})

function row(ts, focus, stress, bpm) {
  return {
    user_id: 's1', name: 'Sam', email: 's@x.y', active_session: { id: 'sess-1' },
    latest_cognitive: { ts, focus, stress }, latest_face: null,
    latest_heart: bpm === undefined ? null : { ts, heart_rate_bpm: bpm, source: 'muse_optics' },
  }
}

function serve(answers) {
  let polls = 0
  mockApi({
    '/api/classes': () => [{ id: 'c1', name: 'Year 4' }],
    '/api/teacher/classes/c1/live': () => {
      const a = answers[Math.min(polls, answers.length - 1)]
      polls += 1
      if (a instanceof Error) throw a
      return [a]
    },
  })
  return () => polls
}

const has = (v) => v !== null && v !== undefined
const bpmLines = (drawn) => drawn.lines.filter(l => String(l.dataKey).startsWith('bpm'))

it('puts a gap between the readings before and after a failed poll', async () => {
  const polls = serve([row('2026-10-02T09:00:00Z', 0.6, 0.3, 70),
                       apiError(500, 'Internal Server Error'),
                       row('2026-10-02T09:00:10Z', 0.4, 0.2, 80)])
  render(<MemoryRouter><Live /></MemoryRouter>)

  // 2 s to the failure, then a 4 s backoff to the recovery.
  await waitFor(() => expect(polls()).toBeGreaterThanOrEqual(3), { timeout: 12_000 })
  await waitFor(() => {
    const { data } = mockDrawn.at(-1)
    expect(data.map(p => p.focus)).toEqual([0.6, null, 0.4])
    // Drawn as calm: stress 0.3 and 0.2 are calm 0.7 and 0.8.
    expect(data.map(p => p.calm)).toEqual([0.7, null, 0.8])
  })
  // The gap is not a reading.
  expect(screen.getByRole('img', { name: /last 2 readings/ })).toBeInTheDocument()

  // Heart rate connects nulls, so it is drawn as one line per side of the gap.
  const { data, lines } = mockDrawn.at(-1)
  const gapAt = data.findIndex(p => p.gap)
  const segments = bpmLines({ lines })
  expect(segments).toHaveLength(2)
  for (const line of segments) {
    const at = data.map((p, i) => (has(p[line.dataKey]) ? i : -1)).filter(i => i >= 0)
    expect(at.length).toBeGreaterThan(0)
    // Never values on both sides of the gap.
    expect(at.every(i => i < gapAt) || at.every(i => i > gapAt)).toBe(true)
    expect(line).toMatchObject({ connectNulls: true, yAxisId: 'bpm', stroke: segments[0].stroke })
  }
  // The sentence and columns read `bpm`, which is unchanged.
  expect(data.map(p => p.bpm)).toEqual([70, null, 80])
}, 20_000)

it('still bridges a rejected heart reading when no poll failed', async () => {
  const polls = serve([row('2026-10-02T09:00:00Z', 0.6, 0.3, 70),
                       row('2026-10-02T09:00:05Z', 0.5, 0.3, null),
                       row('2026-10-02T09:00:10Z', 0.4, 0.2, 80)])
  render(<MemoryRouter><Live /></MemoryRouter>)

  await waitFor(() => expect(polls()).toBeGreaterThanOrEqual(3), { timeout: 12_000 })
  await waitFor(() => expect(mockDrawn.at(-1).data).toHaveLength(3))
  const segments = bpmLines(mockDrawn.at(-1))
  expect(segments).toHaveLength(1)
  expect(segments[0].connectNulls).toBe(true)
  expect(mockDrawn.at(-1).data.map(p => p[segments[0].dataKey])).toEqual([70, null, 80])
}, 20_000)
