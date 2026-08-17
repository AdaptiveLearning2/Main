import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

// The one module in this suite tested unmocked.
//
// Every other test replaces `apiFetch` wholesale, so nothing anywhere else
// exercises the URL it builds, whether the bearer token is attached, or how a
// non-2xx response becomes an Error -- the behaviour all of those tests are
// implicitly trusting. Only `lib/supabase` and `fetch` are mocked here.
//
// `timeoutMs` has the largest share of it for one reason: a request that
// *hangs* is not a request that fails. A rejected promise has a `.catch`
// waiting for it somewhere; a promise that never settles has nothing, and the
// caller waits for ever.

vi.mock('./supabase', async () => await import('../test/mocks/supabase'))

import { apiFetch } from './api'
import { authFns, buildAuthSession, resetSupabaseMock, setSession } from '../test/mocks/supabase'

const BASE = 'http://localhost:8000'

const ok = (body = { ok: true }) => ({
  ok: true,
  status: 200,
  json: () => Promise.resolve(body),
  text: () => Promise.resolve(JSON.stringify(body)),
})

const failing = ({ status = 500, statusText = '', body = '' } = {}) => ({
  ok: false,
  status,
  statusText,
  text: () => Promise.resolve(body),
})

beforeEach(() => {
  vi.useRealTimers()
  resetSupabaseMock()
  setSession(buildAuthSession({ accessToken: 't' }))
  globalThis.fetch = vi.fn().mockResolvedValue(ok())
})

afterEach(() => { vi.useRealTimers() })

const lastCall = () => globalThis.fetch.mock.calls.at(-1)

describe('the request it builds', () => {
  it('prefixes the path with the API base', async () => {
    await apiFetch('/api/sessions')
    expect(lastCall()[0]).toBe(`${BASE}/api/sessions`)
  })

  it('attaches the session access token as a bearer', async () => {
    await apiFetch('/api/sessions')
    expect(lastCall()[1].headers.Authorization).toBe('Bearer t')
  })

  it('omits Authorization entirely when nobody is signed in', async () => {
    // Omitted, not sent empty: `Bearer null` is a malformed credential the
    // backend would reject as bad auth rather than treat as absent.
    setSession(null)
    await apiFetch('/api/questions')
    expect(lastCall()[1].headers).not.toHaveProperty('Authorization')
  })

  it('omits it when reading the session throws, rather than failing the call', async () => {
    // `getAccessToken` swallows this on purpose. A public endpoint must still
    // be reachable when the auth client is unhappy.
    authFns.getSession.mockRejectedValue(new Error('auth client down'))
    await expect(apiFetch('/api/questions')).resolves.toEqual({ ok: true })
    expect(lastCall()[1].headers).not.toHaveProperty('Authorization')
  })

  it('JSON-encodes a body, and defaults the method to GET', async () => {
    await apiFetch('/api/profile/me', { method: 'PUT', body: { grade_level: 5 } })
    const [, opts] = lastCall()
    expect(opts.method).toBe('PUT')
    expect(opts.body).toBe('{"grade_level":5}')
    expect(opts.headers['Content-Type']).toBe('application/json')

    await apiFetch('/api/sessions')
    expect(lastCall()[1].method).toBe('GET')
  })

  it('sends no body when there is none', async () => {
    await apiFetch('/api/sessions')
    expect(lastCall()[1]).not.toHaveProperty('body')
  })
})

describe('an error response', () => {
  it('carries the status code, not just a message', async () => {
    // Callers that tell "this doesn't exist" apart from "the request failed"
    // have only this to go on -- ClassDetail renders a different page for each.
    globalThis.fetch.mockResolvedValue(failing({ status: 404, body: '{"detail":"no such class"}' }))

    await expect(apiFetch('/api/classes/x'))
      .rejects.toMatchObject({ status: 404, message: 'no such class' })
  })

  it('uses the raw text when the body is not JSON', async () => {
    globalThis.fetch.mockResolvedValue(failing({ status: 502, body: 'upstream exploded' }))

    await expect(apiFetch('/api/x'))
      .rejects.toMatchObject({ status: 502, message: 'upstream exploded' })
  })

  it('falls back to the status text when the body is empty', async () => {
    // An Error with an empty message reaches a page as a blank error banner,
    // which reads as a rendering fault rather than as a failed request.
    globalThis.fetch.mockResolvedValue(
      failing({ status: 500, body: '', statusText: 'Internal Server Error' }))

    await expect(apiFetch('/api/x')).rejects.toMatchObject({ message: 'Internal Server Error' })
  })

  it('does not swallow a fetch that rejects outright', async () => {
    globalThis.fetch.mockRejectedValue(new TypeError('Failed to fetch'))
    await expect(apiFetch('/api/x')).rejects.toThrow(/failed to fetch/i)
  })
})

describe('the optional bound', () => {
  it('rejects a request that never settles, once the bound is up', async () => {
    vi.useFakeTimers()
    globalThis.fetch.mockReturnValue(new Promise(() => {}))   // hangs for ever

    const call = apiFetch('/api/profile/me', { timeoutMs: 10_000 })
    const settled = call.then(() => 'resolved', e => e)

    await vi.advanceTimersByTimeAsync(9_999)
    expect(await Promise.race([settled, Promise.resolve('pending')])).toBe('pending')

    await vi.advanceTimersByTimeAsync(2)
    const err = await settled
    expect(err).toBeInstanceOf(Error)
    expect(err.timeout).toBe(true)
  })

  it('aborts the hung request rather than leaving it in flight', async () => {
    // Rejecting alone leaves the request in flight after the caller has given
    // up on it -- a client timeout that cancels nothing just stops you finding
    // out what happened.
    vi.useFakeTimers()
    let signal
    globalThis.fetch.mockImplementation((_url, opts) => {
      signal = opts.signal
      return new Promise(() => {})
    })

    const call = apiFetch('/api/profile/me', { timeoutMs: 5_000 })
    call.catch(() => {})

    await vi.advanceTimersByTimeAsync(5_001)
    expect(signal.aborted).toBe(true)
  })

  it('bounds the token read too, not just the fetch', async () => {
    // `getAccessToken` awaits `supabase.auth.getSession()`, which goes to the
    // network when the access token needs refreshing. A timeout wrapped around
    // `fetch` alone would leave exactly the hang it was added to stop -- and this
    // is the shape that would pass a test written only against a hung `fetch`.
    vi.useFakeTimers()
    authFns.getSession.mockReturnValue(new Promise(() => {}))
    globalThis.fetch.mockResolvedValue(ok())

    const call = apiFetch('/api/profile/me', { timeoutMs: 4_000 })
    const settled = call.then(() => 'resolved', e => e)

    await vi.advanceTimersByTimeAsync(4_001)
    const err = await settled
    expect(err.timeout).toBe(true)
    expect(globalThis.fetch).not.toHaveBeenCalled()
  })

  it('leaves a request without a bound alone', async () => {
    // Opt-in, deliberately. A blanket default would abort the LLM-backed
    // endpoints, which are bounded server-side and can queue first.
    vi.useFakeTimers()
    globalThis.fetch.mockReturnValue(new Promise(() => {}))

    const settled = apiFetch('/api/anything').then(() => 'resolved', () => 'rejected')

    await vi.advanceTimersByTimeAsync(600_000)
    expect(await Promise.race([settled, Promise.resolve('pending')])).toBe('pending')
  })

  it('sends no abort signal when no bound was asked for', async () => {
    await apiFetch('/api/sessions')
    expect(lastCall()[1]).not.toHaveProperty('signal')
  })

  it('does not time out a request that comes back in time', async () => {
    globalThis.fetch.mockResolvedValue(ok({ role: 'admin' }))

    await expect(apiFetch('/api/profile/me', { timeoutMs: 10_000 }))
      .resolves.toEqual({ role: 'admin' })
  })

  it('still surfaces the status on an error response', async () => {
    // The bound must not swallow what callers already rely on.
    globalThis.fetch.mockResolvedValue(
      failing({ status: 403, statusText: 'Forbidden', body: JSON.stringify({ detail: 'nope' }) }))

    await expect(apiFetch('/api/admin/me', { timeoutMs: 10_000 }))
      .rejects.toMatchObject({ status: 403, message: 'nope' })
  })
})
