import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

// The only test file that runs apiFetch for real, with only `lib/supabase`
// and `fetch` mocked. Every other test replaces apiFetch wholesale, so this
// is the sole place the URL, the bearer token, and error handling are
// actually exercised.
//
// Timeouts get the most coverage because a hung request is not the same as
// a failed one: a rejected promise has a `.catch` waiting; one that never
// settles does not, and the caller waits forever.

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
    // Must be omitted, not sent as `Bearer null` — that reads as a bad
    // credential, not as no credential.
    setSession(null)
    await apiFetch('/api/questions')
    expect(lastCall()[1].headers).not.toHaveProperty('Authorization')
  })

  it('omits it when reading the session throws, rather than failing the call', async () => {
    // `getAccessToken` swallows this on purpose, so a public endpoint stays
    // reachable even when the auth client is broken.
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
    // Callers need the status code to tell "not found" from "request failed".
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
    // An empty error message would render as a blank banner, which looks
    // like a rendering bug rather than a failed request.
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
    globalThis.fetch.mockReturnValue(new Promise(() => {}))   // never resolves

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
    // Rejecting alone leaves the request running in the background — the
    // timeout must also abort it.
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
    // `getAccessToken` can itself hit the network refreshing the token, so
    // the timeout has to cover that too, not just `fetch`.
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
    // Opt-in on purpose: a default timeout would abort the LLM-backed
    // endpoints, which can legitimately take longer and are bounded server-side.
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
