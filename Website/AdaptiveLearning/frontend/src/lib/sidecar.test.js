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

describe('pushStatus, enabled', () => {
  it('reports enabled:false rather than omitting it', async () => {
    // A reachable sidecar that is deliberately not pushing (PUSH_ENABLED off
    // while the backend is in push mode) means nobody is writing this session
    // at all. The caller has to be able to tell that from a healthy one --
    // merging a flat `reachable: true` over the top rendered it as normal.
    mockFetch(async () => ok({ status: 'ok', data: { enabled: false } }))

    const out = await sidecar.pushStatus()

    expect(out.enabled).toBe(false)
    expect(out.running).toBeUndefined()
  })
})

describe('startPush retry', () => {
  it('can be called again after a failure and succeed', async () => {
    // The sidecar coming up after the page is the normal sequence on a
    // student's machine: they open the lesson, then start the local app. A
    // single attempt meant nothing was pushed for the rest of the session.
    let calls = 0
    mockFetch(async () => {
      calls += 1
      if (calls === 1) throw new TypeError('Failed to fetch')
      return ok({ status: 'pushing' })
    })

    await expect(sidecar.startPush('s')).rejects.toThrow()
    await expect(sidecar.startPush('s')).resolves.toMatchObject({ status: 'pushing' })
  })
})

describe('startPush with a supplied token', () => {
  it('does not call getSession when given one', async () => {
    // The auth-lock deadlock. supabase-js v2 holds an internal lock while
    // dispatching onAuthStateChange, and getSession() waits on that same lock,
    // so awaiting it from inside the callback hangs forever -- the sidecar
    // keeps the expired token and every push 401s for the rest of the lesson,
    // silently. The refresh handler passes the session it was handed.
    const fetchSpy = mockFetch(async () => ok({ status: 'pushing' }))
    getSession.mockImplementation(() => {
      throw new Error('getSession must not be called from the refresh path')
    })

    await sidecar.startPush('sess-1', 'refreshed-jwt')

    expect(getSession).not.toHaveBeenCalled()
    expect(JSON.parse(fetchSpy.mock.calls[0][1].body).access_token).toBe('refreshed-jwt')
  })

  it('still falls back to getSession when not given one', async () => {
    const fetchSpy = mockFetch(async () => ok({ status: 'pushing' }))

    await sidecar.startPush('sess-1')

    expect(JSON.parse(fetchSpy.mock.calls[0][1].body).access_token).toBe('student-jwt')
  })
})

describe('stopPushOnUnload', () => {
  it('uses keepalive so the request outlives the page', async () => {
    // Effect cleanup does not run on a tab close or hard refresh, which is how
    // most lessons end. Without a stop that survives teardown the sidecar keeps
    // the bearer token and keeps recording for up to an hour after the student
    // walked away -- a consent problem, not a tidiness one.
    const fetchSpy = mockFetch(async () => ok())

    await sidecar.stopPushOnUnload()

    const [url, opts] = fetchSpy.mock.calls[0]
    expect(url).toContain('/api/v1/push/stop')
    expect(opts.keepalive).toBe(true)
  })

  it('carries the sidecar token when one is configured', async () => {
    // sendBeacon is the usual tool for an unload request and is no use here:
    // it cannot set an Authorization header, and the sidecar requires one.
    vi.stubEnv('VITE_EEG_LOCAL_TOKEN', 'local-tok')
    vi.resetModules()
    const fresh = await import('./sidecar')
    const fetchSpy = mockFetch(async () => ok())

    await fresh.stopPushOnUnload()

    expect(fetchSpy.mock.calls[0][1].headers.Authorization).toBe('Bearer local-tok')
    vi.unstubAllEnvs()
  })

  it('never throws from a page that is going away', async () => {
    mockFetch(() => { throw new TypeError('Failed to fetch') })

    await expect(sidecar.stopPushOnUnload()).resolves.toBeUndefined()
  })
})
