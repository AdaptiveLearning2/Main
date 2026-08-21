import { useEffect } from 'react'
import { render, waitFor } from '@testing-library/react'
import { vi } from 'vitest'

// The facial-recognition opt-out means facial data must not be read, not just
// not shown. /api/parent/children reads face_signals for every linked child
// unless told otherwise.

vi.mock('../../lib/api', () => ({ apiFetch: vi.fn() }))
vi.mock('react-router-dom', () => ({ useParams: () => ({ id: 'child-1' }) }))

// Stands in for the real report so only the name source is under test.
vi.mock('../../components/reports/StudentProgressReport', () => ({
  default: ({ nameFetch }) => {
    useEffect(() => { nameFetch?.().catch(() => {}) }, [nameFetch])
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
