import { useEffect } from 'react'
import { render, waitFor } from '@testing-library/react'
import { vi } from 'vitest'

// The facial-recognition opt-out means facial data must not be read, not just
// not shown. /api/parent/children reads face_signals for every linked child
// unless told otherwise.

vi.mock('../../lib/api', () => ({ apiFetch: vi.fn() }))
vi.mock('react-router-dom', () => ({ useParams: () => ({ id: 'child-1' }) }))

// Stands in for the real report so only the name source is under test.
// Records what the page asked for, so a panel this route is meant to mount
// cannot be dropped without a test noticing.
const mockReportProps = {}
vi.mock('../../components/reports/StudentProgressReport', () => ({
  default: (props) => {
    Object.assign(mockReportProps, props)
    useEffect(() => { props.nameFetch?.().catch(() => {}) }, [props.nameFetch])
    return <div>report</div>
  },
}))

const { apiFetch } = await import('../../lib/api')
const { default: ChildDetail } = await import('./ChildDetail')

beforeEach(() => {
  apiFetch.mockReset()
  apiFetch.mockResolvedValue([{ user_id: 'child-1', name: 'Ada' }])
})

it('looks the name up without reading facial data', async () => {
  render(<ChildDetail />)
  await waitFor(() => expect(apiFetch).toHaveBeenCalled())
  const url = String(apiFetch.mock.calls[0][0])
  expect(url).toContain('/api/parent/children')
  expect(url).toContain('include_face=false')
})

/**
 * Both panels are mounted on the parent route. Not behind a switch here: the
 * parent surface has no "Hide sensor data" preference -- that is the teacher's
 * decluttering control and not a privacy boundary.
 *
 * Asserted on the props rather than on rendered markup, because this file
 * stands the report in for a double: what this page decides is which panels
 * the report is asked for, and the panels themselves are tested where they
 * live.
 */
it('asks for both the strategies and the chart summary', async () => {
  render(<ChildDetail />)
  await waitFor(() => expect(mockReportProps.showStrategies).toBe(true))
  expect(mockReportProps.showChartSummary).toBe(true)
})
