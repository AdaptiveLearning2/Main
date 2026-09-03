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

// ─── honouring Retry-After ────────────────────────────────────────────────
//
// The backend has always sent `Retry-After: 5` with the 503 it raises when
// the generation waiter cap is full, and until this existed nothing read it:
// a student refused because their class started together saw an error screen
// rather than a pause. Measured at the shipped cap, a simultaneous start of
// 30 students served 40% of them.

const refused = (retryAfter) => ({
  ok: false,
  status: 503,
  statusText: 'Service Unavailable',
  headers: {
    get: (name) => (name === 'Retry-After' && retryAfter != null
      ? String(retryAfter) : null),
  },
  text: () => Promise.resolve(JSON.stringify({ detail: 'Too many questions' })),
})

describe('Retry-After', () => {
  it('retries a 503 that asked us to come back, and returns the result', async () => {
    globalThis.fetch = vi.fn()
      .mockResolvedValueOnce(refused(0))
      .mockResolvedValueOnce(ok({ question: 'q' }))

    await expect(apiFetch('/api/generate-question')).resolves.toEqual({ question: 'q' })
    expect(globalThis.fetch).toHaveBeenCalledTimes(2)
  })

  it('does not retry a 503 that asked for nothing', async () => {
    // Only an explicit Retry-After is an invitation. A bare 503 is a failure,
    // and retrying every one of them would turn any outage into a stampede.
    globalThis.fetch = vi.fn().mockResolvedValue(refused(null))

    await expect(apiFetch('/api/generate-question')).rejects.toMatchObject({ status: 503 })
    expect(globalThis.fetch).toHaveBeenCalledTimes(1)
  })

  it('never retries a write, however politely it was refused', async () => {
    // GET-only is what makes this safe by construction rather than by audit:
    // a replayed POST could repeat a side effect. Both generation endpoints
    // are GETs, so the restriction costs nothing.
    globalThis.fetch = vi.fn().mockResolvedValue(refused(0))

    await expect(apiFetch('/api/sessions', { method: 'POST', body: { a: 1 } }))
      .rejects.toMatchObject({ status: 503 })
    expect(globalThis.fetch).toHaveBeenCalledTimes(1)
  })

  it('gives up rather than retrying forever', async () => {
    globalThis.fetch = vi.fn().mockResolvedValue(refused(0))

    await expect(apiFetch('/api/generate-question')).rejects.toMatchObject({ status: 503 })
    expect(globalThis.fetch).toHaveBeenCalledTimes(3)   // the try, then two retries
  })

  it('spreads the retry instead of coming back in lockstep', async () => {
    // The part that is easy to omit and defeats the change: every browser
    // refused in the same burst holds the same `Retry-After`, so honouring
    // it exactly reforms the burst one round later.
    const random = vi.spyOn(Math, 'random').mockReturnValue(0.5)
    vi.useFakeTimers()
    globalThis.fetch = vi.fn()
      .mockResolvedValueOnce(refused(4))
      .mockResolvedValueOnce(ok())

    const pending = apiFetch('/api/generate-question')
    await vi.advanceTimersByTimeAsync(1999)
    expect(globalThis.fetch).toHaveBeenCalledTimes(1)   // 4s x 0.5 = 2s
    await vi.advanceTimersByTimeAsync(2)
    await pending
    expect(globalThis.fetch).toHaveBeenCalledTimes(2)
    expect(random).toHaveBeenCalled()
    vi.useRealTimers()
  })

  it('will not be parked for an hour by whatever the header says', async () => {
    // `Retry-After` is a request, not an instruction.
    vi.spyOn(Math, 'random').mockReturnValue(1)
    vi.useFakeTimers()
    globalThis.fetch = vi.fn()
      .mockResolvedValueOnce(refused(3600))
      .mockResolvedValueOnce(ok())

    const pending = apiFetch('/api/generate-question')
    await vi.advanceTimersByTimeAsync(10_000)
    await pending
    expect(globalThis.fetch).toHaveBeenCalledTimes(2)
    vi.useRealTimers()
  })

  it('leaves every other failure exactly as it was', async () => {
    globalThis.fetch = vi.fn().mockResolvedValue(
      failing({ status: 500, body: 'boom' }))

    await expect(apiFetch('/api/generate-question')).rejects.toMatchObject({ status: 500 })
    expect(globalThis.fetch).toHaveBeenCalledTimes(1)
  })
})
