import { beforeEach, describe, expect, it } from 'vitest'
import { apiFetch, apiError, mockApi, overrideApi, pending, resetApi } from './apiFetch'

// Every page test reads its data through this router, so a bug here would
// surface as a bug in whatever page happens to be under test.

beforeEach(() => { resetApi() })

describe('routing', () => {
  it('matches an exact path', async () => {
    mockApi({ '/api/sessions': [1, 2] })
    await expect(apiFetch('/api/sessions')).resolves.toEqual([1, 2])
  })

  it('matches on method when the key names one', async () => {
    mockApi({
      '/api/profile/me': { name: 'read' },
      'PUT /api/profile/me': { name: 'written' },
    })

    await expect(apiFetch('/api/profile/me')).resolves.toEqual({ name: 'read' })
    await expect(apiFetch('/api/profile/me', { method: 'PUT' }))
      .resolves.toEqual({ name: 'written' })
  })

  it('prefers the method-scoped route however the keys are ordered', async () => {
    // Same routes as above, keys in the opposite order — must not depend on
    // write order.
    mockApi({
      'PUT /api/profile/me': { name: 'written' },
      '/api/profile/me': { name: 'read' },
    })

    await expect(apiFetch('/api/profile/me')).resolves.toEqual({ name: 'read' })
    await expect(apiFetch('/api/profile/me', { method: 'PUT' }))
      .resolves.toEqual({ name: 'written' })
  })

  it('treats a route with no method as matching any', async () => {
    mockApi({ '/api/x': 'either' })
    await expect(apiFetch('/api/x', { method: 'DELETE' })).resolves.toBe('either')
  })

  it('accepts a regex and a predicate', async () => {
    mockApi([
      { match: /^\/api\/stats\//, handler: 'by-regex' },
      { match: (p) => p.includes('needle'), handler: 'by-predicate' },
    ])

    await expect(apiFetch('/api/stats/stu-1')).resolves.toBe('by-regex')
    await expect(apiFetch('/api/haystack/needle')).resolves.toBe('by-predicate')
  })

  it('passes the path and options to a handler function', async () => {
    mockApi({ '/api/echo': (path, opts) => ({ path, method: opts.method }) })
    await expect(apiFetch('/api/echo', { method: 'POST' }))
      .resolves.toEqual({ path: '/api/echo', method: 'POST' })
  })
})

describe('an unrouted path', () => {
  it('throws rather than resolving undefined', async () => {
    // A silent `undefined` would look like a successful empty read, masking
    // a gap in test setup as the bug it was meant to catch.
    mockApi({ '/api/known': 1 })
    await expect(apiFetch('/api/unknown')).rejects.toThrow(/no route for GET \/api\/unknown/i)
  })

  it('names the routes it did have, and the method it was called with', async () => {
    mockApi({ 'PUT /api/known': 1 })
    await expect(apiFetch('/api/known', { method: 'POST' }))
      .rejects.toThrow(/POST \/api\/known[\s\S]*PUT \/api\/known/)
  })
})

describe('overrides', () => {
  it('take precedence over the base table', async () => {
    mockApi({ '/api/sessions': 'happy path' })
    overrideApi('/api/sessions', () => { throw apiError(500, 'down') })

    await expect(apiFetch('/api/sessions')).rejects.toMatchObject({ status: 500 })
  })

  it('leave the other routes alone', async () => {
    // Overriding one endpoint should not require restating the rest.
    mockApi({ '/api/a': 'a', '/api/b': 'b' })
    overrideApi('/api/a', () => { throw apiError(503) })

    await expect(apiFetch('/api/b')).resolves.toBe('b')
  })

  it('can be scoped to one method', async () => {
    mockApi({ '/api/flags': 'read' })
    overrideApi('/api/flags', () => { throw apiError(409) }, 'PUT')

    await expect(apiFetch('/api/flags')).resolves.toBe('read')
    await expect(apiFetch('/api/flags', { method: 'PUT' })).rejects.toMatchObject({ status: 409 })
  })
})

describe('helpers', () => {
  it('apiError carries the status the real client attaches', async () => {
    // Callers branch on `.status` to tell "not found" from "request failed";
    // a bare Error would only exercise the second path.
    mockApi({ '/api/missing': () => { throw apiError(404, 'no such class') } })

    await expect(apiFetch('/api/missing'))
      .rejects.toMatchObject({ status: 404, message: 'no such class' })
  })

  it('pending never settles, so a page stays in its loading state', async () => {
    mockApi({ '/api/slow': pending() })
    let settled = false
    apiFetch('/api/slow').finally(() => { settled = true })

    await Promise.resolve()
    await Promise.resolve()
    expect(settled).toBe(false)
  })
})

it('records calls, so the router does not replace asserting on them', async () => {
  mockApi({ 'POST /api/sessions/start': { id: 's1' } })
  await apiFetch('/api/sessions/start', { method: 'POST', body: { mode: 'solo' } })

  expect(apiFetch).toHaveBeenCalledWith('/api/sessions/start',
    { method: 'POST', body: { mode: 'solo' } })
})

it('resetApi clears calls and routes but keeps the router working', async () => {
  // `mockReset()` would drop the implementation too, leaving a mock that
  // resolves undefined for everything.
  mockApi({ '/api/x': 1 })
  await apiFetch('/api/x')
  resetApi()

  expect(apiFetch).not.toHaveBeenCalled()
  mockApi({ '/api/y': 2 })
  await expect(apiFetch('/api/y')).resolves.toBe(2)
})
