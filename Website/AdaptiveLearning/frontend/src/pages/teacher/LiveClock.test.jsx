/** The shared "Xs ago" clock is current on the first frame, not one tick late. */
import { it, expect, vi, afterEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'

vi.mock('../../lib/api', async () => await import('../../test/mocks/apiFetch'))
vi.mock('../../lib/signalAge', async (importOriginal) => {
  const actual = await importOriginal()
  return { ...actual, formatAge: vi.fn(actual.formatAge) }
})

import { mockApi, resetApi } from '../../test/mocks/apiFetch'
import { formatAge } from '../../lib/signalAge'
import Live from './Live'

afterEach(() => {
  vi.useRealTimers()
  resetApi()
})

it('ages a reading against the current time on the first render after the clock sat idle', async () => {
  // Only Date is faked, and far past anything the idle clock last read; polls keep real timers.
  vi.useFakeTimers({ toFake: ['Date'] })
  const now = Date.parse('2031-01-01T12:00:00Z')
  vi.setSystemTime(now)
  mockApi({
    '/api/classes': () => [{ id: 'c1', name: 'Year 4' }],
    '/api/teacher/classes/c1/live': () => [{
      user_id: 's1', name: 'Sam', email: 's@x.y', active_session: { id: 'sess-1' },
      latest_cognitive: { ts: new Date(now - 10_000).toISOString(), focus: 0.6, stress: 0.3 },
      latest_face: null, latest_heart: null,
    }],
  })
  render(<MemoryRouter><Live /></MemoryRouter>)

  expect((await screen.findByText(/Headband on/)).textContent).toMatch(/10s ago/)
  // Every age a badge rendered, the first frame included; a stale clock reads as "0s ago".
  const ages = formatAge.mock.calls.map(([ms]) => ms)
  expect(ages.length).toBeGreaterThan(0)
  expect(ages).toEqual(ages.map(() => 10_000))
})
