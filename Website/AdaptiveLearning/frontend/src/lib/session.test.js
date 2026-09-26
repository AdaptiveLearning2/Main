import { it, expect, beforeEach, vi } from 'vitest'

vi.mock('./api', async () => await import('../test/mocks/apiFetch'))
vi.mock('sonner', () => ({ toast: { error: vi.fn(), success: vi.fn() } }))

import { toast } from 'sonner'
import { apiError, apiFetch, mockApi, resetApi } from '../test/mocks/apiFetch'
import { fetchSessionList, markEegStarted, recordAnswer } from './session'

beforeEach(() => { resetApi(); vi.mocked(toast.error).mockClear() })

const ANSWER = { sessionId: 's1', questionId: 'q1', selectedIndex: 2, correct: true }

it('reports a session closed under the page as ended, without a toast', async () => {
  mockApi({ 'POST /api/sessions/s1/answer': () => { throw apiError(409, 'This session has ended') } })

  await expect(recordAnswer(ANSWER)).resolves.toEqual({ ended: true })
  expect(toast.error).not.toHaveBeenCalled()
})

it('reports a push EEG start for the session it names', async () => {
  mockApi({ 'POST /api/sessions/s1/eeg-started': () => ({ ok: true }) })

  await expect(markEegStarted('s1')).resolves.toBe(true)
  expect(apiFetch).toHaveBeenCalledWith('/api/sessions/s1/eeg-started', { method: 'POST' })
})

it('answers false for a failed EEG-start report, without a toast', async () => {
  mockApi({ 'POST /api/sessions/s1/eeg-started': () => { throw apiError(503) } })

  await expect(markEegStarted('s1')).resolves.toBe(false)
  await expect(markEegStarted(null)).resolves.toBe(false)
  expect(toast.error).not.toHaveBeenCalled()
  expect(apiFetch).toHaveBeenCalledTimes(1)
})

it('still toasts and returns null for any other failure', async () => {
  mockApi({ 'POST /api/sessions/s1/answer': () => { throw apiError(500) } })

  await expect(recordAnswer(ANSWER)).resolves.toBeNull()
  expect(toast.error).toHaveBeenCalledWith('That answer could not be saved.')
})

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
