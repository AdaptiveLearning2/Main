import { useEffect } from 'react'
import { render, waitFor } from '@testing-library/react'
import { vi } from 'vitest'

// The facial opt-out means facial data is not read, not just not shown.

vi.mock('../../lib/api', () => ({ apiFetch: vi.fn() }))
let mockLinkState = null
vi.mock('react-router-dom', () => ({
  useParams: () => ({ id: 'child-1' }),
  useLocation: () => ({ state: mockLinkState }),
}))

// Stands in for the real report and records the props the page passed.
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
  mockLinkState = null
  for (const k of Object.keys(mockReportProps)) delete mockReportProps[k]
  apiFetch.mockReset()
  apiFetch.mockResolvedValue([{ user_id: 'child-1', name: 'Ada' }])
})

it('takes the name from the dashboard link and reads no children list', async () => {
  // That list is the parent's heaviest read, and only the name was wanted from it.
  mockLinkState = { name: 'Ada' }
  render(<ChildDetail />)
  await waitFor(() => expect(mockReportProps.initialName).toBe('Ada'))
  expect(mockReportProps.nameFetch).toBeUndefined()
  expect(apiFetch).not.toHaveBeenCalled()
})

it('looks the name up without reading facial data', async () => {
  render(<ChildDetail />)
  await waitFor(() => expect(apiFetch).toHaveBeenCalled())
  const url = String(apiFetch.mock.calls[0][0])
  expect(url).toContain('/api/parent/children')
  expect(url).toContain('include_face=false')
})

/** Both panels mount on the parent route, which has no "Hide sensor data" switch; asserted on props. */
it('asks for both the strategies and the chart summary', async () => {
  render(<ChildDetail />)
  await waitFor(() => expect(mockReportProps.showStrategies).toBe(true))
  expect(mockReportProps.showChartSummary).toBe(true)
})
