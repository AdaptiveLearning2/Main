import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'

vi.mock('./api', async () => await import('../test/mocks/apiFetch'))

import { apiFetch, mockApi, resetApi } from '../test/mocks/apiFetch'
import { fetchQuestionsCached, _resetForTests } from './questionsCache'

beforeEach(() => {
  resetApi()
  _resetForTests()
  vi.useFakeTimers()
  mockApi({ '/api/questions?limit=1000': [{ id: 'q1' }] })
})

afterEach(() => {
  vi.useRealTimers()
})

describe('fetchQuestionsCached', () => {
  it('serves three simultaneous mounts from a single request', async () => {
    // Analytics and Questions can both mount in the same tick during teacher
    // navigation. This is the direct regression test for that: without the
    // in-flight dedup, each call fires its own fetch before any resolves.
    const [a, b, c] = await Promise.all([
      fetchQuestionsCached(1000),
      fetchQuestionsCached(1000),
      fetchQuestionsCached(1000),
    ])

    expect(apiFetch).toHaveBeenCalledTimes(1)
    expect(a).toEqual([{ id: 'q1' }])
    expect(b).toBe(a)
    expect(c).toBe(a)
  })

  it('serves a second call from the cache once the first has resolved', async () => {
    await fetchQuestionsCached(1000)
    await fetchQuestionsCached(1000)

    expect(apiFetch).toHaveBeenCalledTimes(1)
  })

  it('refetches once the TTL has passed', async () => {
    await fetchQuestionsCached(1000)

    vi.advanceTimersByTime(30_001)
    await fetchQuestionsCached(1000)

    expect(apiFetch).toHaveBeenCalledTimes(2)
  })

  it('keys the cache by limit, so a different limit is not served stale', async () => {
    mockApi({
      '/api/questions?limit=1000': [{ id: 'q1' }],
      '/api/questions?limit=5': [{ id: 'q2' }],
    })

    const big = await fetchQuestionsCached(1000)
    const small = await fetchQuestionsCached(5)

    expect(apiFetch).toHaveBeenCalledTimes(2)
    expect(big).toEqual([{ id: 'q1' }])
    expect(small).toEqual([{ id: 'q2' }])
  })

  it('does not cache a failure, so retry() genuinely refetches', async () => {
    mockApi({
      '/api/questions?limit=1000': () => { throw new Error('backend down') },
    })

    await expect(fetchQuestionsCached(1000)).rejects.toThrow('backend down')

    mockApi({ '/api/questions?limit=1000': [{ id: 'q1' }] })
    const result = await fetchQuestionsCached(1000)

    expect(result).toEqual([{ id: 'q1' }])
    expect(apiFetch).toHaveBeenCalledTimes(2)
  })
})
