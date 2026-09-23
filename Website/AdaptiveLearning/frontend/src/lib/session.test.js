import { it, expect, beforeEach, vi } from 'vitest'

vi.mock('./api', async () => await import('../test/mocks/apiFetch'))
vi.mock('sonner', () => ({ toast: { error: vi.fn(), success: vi.fn() } }))

import { apiFetch, mockApi, resetApi } from '../test/mocks/apiFetch'
import { fetchSessionList } from './session'

beforeEach(() => { resetApi() })

it('reads the capped list with its count', async () => {
  mockApi({ '/api/sessions': () => ({ sessions: [{ id: 's1' }], total: 431, truncated: true }) })

  await expect(fetchSessionList()).resolves.toEqual(
    { sessions: [{ id: 's1' }], total: 431, truncated: true })
})

it('asks for fewer rows when told to', async () => {
  mockApi({ '/api/sessions?limit=4': () => ({ sessions: [], total: 0, truncated: false }) })

  await fetchSessionList({ limit: 4 })

  expect(apiFetch).toHaveBeenCalledWith('/api/sessions?limit=4')
})

it.each([
  ['a bare list, as an older backend sends', [{ id: 's1' }]],
  ['no body at all', null],
  ['an object with no list in it', { total: 3 }],
])('rejects %s rather than calling it no sessions', async (_name, body) => {
  mockApi({ '/api/sessions': () => body })

  await expect(fetchSessionList()).rejects.toThrow(/shape/)
})

it('keeps an absent count as unknown, not as zero or as the rows it got', async () => {
  mockApi({ '/api/sessions': () => ({ sessions: [{ id: 's1' }, { id: 's2' }], total: null }) })

  await expect(fetchSessionList()).resolves.toEqual(
    { sessions: [{ id: 's1' }, { id: 's2' }], total: null, truncated: null })
})
