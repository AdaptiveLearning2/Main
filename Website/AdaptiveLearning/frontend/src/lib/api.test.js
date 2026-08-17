import { vi } from 'vitest'
import { apiFetch } from './api'

// `timeoutMs` exists for one reason: a request that *hangs* is not a request
// that fails. A rejected promise has a `.catch` waiting for it somewhere; a
// promise that never settles has nothing, and the caller waits for ever.

const getSession = vi.fn()

vi.mock('./supabase', () => ({
  supabase: { auth: { getSession: (...a) => getSession(...a) } },
}))

const ok = (body = { ok: true }) => ({
  ok: true,
  status: 200,
  json: () => Promise.resolve(body),
  text: () => Promise.resolve(JSON.stringify(body)),
})

beforeEach(() => {
  vi.useRealTimers()
  getSession.mockReset()
  getSession.mockResolvedValue({ data: { session: { access_token: 't' } } })
  global.fetch = vi.fn()
})

afterEach(() => { vi.useRealTimers() })

it('rejects a request that never settles, once the bound is up', async () => {
  vi.useFakeTimers()
  global.fetch.mockReturnValue(new Promise(() => {}))   // hangs for ever

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
  vi.useFakeTimers()
  let signal
  global.fetch.mockImplementation((_url, opts) => {
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
  getSession.mockReturnValue(new Promise(() => {}))
  global.fetch.mockResolvedValue(ok())

  const call = apiFetch('/api/profile/me', { timeoutMs: 4_000 })
  const settled = call.then(() => 'resolved', e => e)

  await vi.advanceTimersByTimeAsync(4_001)
  const err = await settled
  expect(err.timeout).toBe(true)
  expect(global.fetch).not.toHaveBeenCalled()
})

it('leaves a request without a bound alone', async () => {
  // Opt-in, deliberately. A blanket default would abort the LLM-backed
  // endpoints, which are bounded server-side and can queue first.
  vi.useFakeTimers()
  global.fetch.mockReturnValue(new Promise(() => {}))

  const settled = apiFetch('/api/anything').then(() => 'resolved', () => 'rejected')

  await vi.advanceTimersByTimeAsync(600_000)
  expect(await Promise.race([settled, Promise.resolve('pending')])).toBe('pending')
})

it('does not time out a request that comes back in time', async () => {
  global.fetch.mockResolvedValue(ok({ role: 'admin' }))

  await expect(apiFetch('/api/profile/me', { timeoutMs: 10_000 }))
    .resolves.toEqual({ role: 'admin' })
})

it('still surfaces the status on an error response', async () => {
  // The bound must not swallow what callers already rely on.
  global.fetch.mockResolvedValue({
    ok: false, status: 403, statusText: 'Forbidden',
    text: () => Promise.resolve(JSON.stringify({ detail: 'nope' })),
  })

  await expect(apiFetch('/api/admin/me', { timeoutMs: 10_000 }))
    .rejects.toMatchObject({ status: 403, message: 'nope' })
})
