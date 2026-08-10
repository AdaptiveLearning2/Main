import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'

const getSession = vi.fn()
vi.mock('./supabase', () => ({ supabase: { auth: { getSession: () => getSession() } } }))

let sidecar
beforeEach(async () => {
  vi.resetModules()
  getSession.mockResolvedValue({ data: { session: { access_token: 'student-jwt' } } })
  sidecar = await import('./sidecar')
})

afterEach(() => { vi.restoreAllMocks() })

function mockFetch(impl) {
  const spy = vi.fn(impl)
  global.fetch = spy
  return spy
}

const ok = (body = {}) => ({
  ok: true, status: 200, statusText: 'OK', text: async () => JSON.stringify(body),
})

describe('startPush', () => {
  it("sends the student's own token, not the sidecar's", async () => {
    // Two different credentials with different jobs. The sidecar token
    // authenticates *this page* to a loopback process and is in the bundle; the
    // access token is the student's backend credential and is what lets the
    // sidecar post as them. Sending the wrong one either fails to authorise or,
    // worse, hands a shared token to something that posts with it.
    const fetchSpy = mockFetch(async () => ok({ status: 'pushing' }))

    await sidecar.startPush('sess-1')

    const [url, opts] = fetchSpy.mock.calls[0]
    expect(url).toContain('/api/v1/push/start')
    expect(JSON.parse(opts.body)).toEqual({
      session_id: 'sess-1', access_token: 'student-jwt',
    })
  })

  it('refuses to start when nobody is signed in', async () => {
    // Rather than posting `access_token: undefined` and letting the sidecar
    // hold a credential that authorises nothing -- which would look like a
    // working session producing no rows.
    getSession.mockResolvedValue({ data: { session: null } })
    mockFetch(async () => ok())

    await expect(sidecar.startPush('sess-1')).rejects.toThrow(/not signed in/i)
  })

  it('surfaces the 409 the sidecar answers when push is off', async () => {
    // The sidecar refuses to be a second writer alongside a poller. The caller
    // has to be able to tell that apart from a network failure, because one is
    // a configuration statement and the other is an outage.
    mockFetch(async () => ({
      ok: false, status: 409, statusText: 'Conflict',
      text: async () => JSON.stringify({ detail: 'PUSH_ENABLED=false' }),
    }))

    await expect(sidecar.startPush('s')).rejects.toMatchObject({ status: 409 })
  })
})

describe('sidecarAlive', () => {
  it('is false rather than throwing when nothing is listening', async () => {
    // The ordinary case on a machine with no headband and no camera. A throw
    // here would take out whatever rendered it.
    mockFetch(async () => { throw new TypeError('Failed to fetch') })

    await expect(sidecar.sidecarAlive()).resolves.toBe(false)
  })
})

describe('pushStatus', () => {
  it('unwraps the envelope and keeps the recorded counts', async () => {
    mockFetch(async () => ok({
      status: 'ok',
      data: { enabled: true, running: true, recorded: { cognitive: 12 }, queued: { cognitive: 3 } },
    }))

    const out = await sidecar.pushStatus()

    expect(out.running).toBe(true)
    expect(out.recorded.cognitive).toBe(12)
  })
})
