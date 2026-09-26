import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { DEFAULT_SIDECAR_URL } from './origins'

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
    // The access token lets the sidecar post as the student; the sidecar token only authenticates the page.
    const fetchSpy = mockFetch(async () => ok({ status: 'pushing' }))

    await sidecar.startPush('sess-1')

    const [url, opts] = fetchSpy.mock.calls[0]
    expect(url).toContain('/api/v1/push/start')
    expect(JSON.parse(opts.body)).toEqual({
      session_id: 'sess-1', access_token: 'student-jwt',
    })
  })

  it('falls back to the default the Pages CSP allows, whatever a local .env says', async () => {
    vi.stubEnv('VITE_EEG_LOCAL_URL', '')
    vi.resetModules()
    try {
      const fresh = await import('./sidecar')
      const fetchSpy = mockFetch(async () => ok({ status: 'pushing' }))
      await fresh.startPush('sess-1')
      expect(fetchSpy.mock.calls[0][0]).toBe(`${DEFAULT_SIDECAR_URL}/api/v1/push/start`)
    } finally {
      vi.unstubAllEnvs()
    }
  })

  it('refuses to start when nobody is signed in', async () => {
    // `access_token: undefined` would be a session that silently writes no rows.
    getSession.mockResolvedValue({ data: { session: null } })
    mockFetch(async () => ok())

    await expect(sidecar.startPush('sess-1')).rejects.toThrow(/not signed in/i)
  })

  it('surfaces the 409 the sidecar answers when push is off', async () => {
    // "Push is disabled" (config) is not a network failure.
    mockFetch(async () => ({
      ok: false, status: 409, statusText: 'Conflict',
      text: async () => JSON.stringify({ detail: 'PUSH_ENABLED=false' }),
    }))

    await expect(sidecar.startPush('s')).rejects.toMatchObject({ status: 409 })
  })
})

describe('sidecarAlive', () => {
  it('is false rather than throwing when nothing is listening', async () => {
    // Normal on a machine with no headband or camera.
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
    // A reachable sidecar that isn't pushing means nobody is writing this session.
    mockFetch(async () => ok({ status: 'ok', data: { enabled: false } }))

    const out = await sidecar.pushStatus()

    expect(out.enabled).toBe(false)
    expect(out.running).toBeUndefined()
  })
})

describe('startPush retry', () => {
  it('can be called again after a failure and succeed', async () => {
    // The lesson often opens before the local app starts.
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
    // getSession() inside onAuthStateChange deadlocks on supabase-js's auth lock.
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
    // Effect cleanup does not run on tab close; a missed stop keeps recording.
    const fetchSpy = mockFetch(async () => ok())

    await sidecar.stopPushOnUnload()

    const [url, opts] = fetchSpy.mock.calls[0]
    expect(url).toContain('/api/v1/push/stop')
    expect(opts.keepalive).toBe(true)
  })

  it('carries the sidecar token when one is configured', async () => {
    // Not sendBeacon: it cannot set an Authorization header.
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

describe('releasePushIfIdle', () => {
  // `/api/v1/push/stop` clears every device, so no device may call it unconditionally.

  const devicesReply = (list) => ok({ status: 'ok', data: list })

  it('leaves the push client alone while another device is still streaming', async () => {
    const seen = []
    mockFetch(async (url) => {
      seen.push(String(url))
      if (String(url).includes('/devices')) {
        return devicesReply([{ device_id: 'default', kind: 'muse', running: false },
                             { device_id: 'camera', kind: 'face', running: true }])
      }
      return ok({})
    })

    const out = await sidecar.releasePushIfIdle()

    expect(out.stopped).toBe(false)
    expect(seen.some(u => u.includes('/push/stop'))).toBe(false)
  })

  it('stops it once nothing is streaming', async () => {
    // Otherwise the push client keeps holding the student's access token.
    const seen = []
    mockFetch(async (url) => {
      seen.push(String(url))
      if (String(url).includes('/devices')) {
        return devicesReply([{ device_id: 'default', kind: 'muse', running: false },
                             { device_id: 'camera', kind: 'face', running: false }])
      }
      return ok({})
    })

    const out = await sidecar.releasePushIfIdle()

    expect(out.stopped).toBe(true)
    expect(seen.some(u => u.includes('/push/stop'))).toBe(true)
  })

  it('stops it when it cannot tell, rather than leaving a token held', async () => {
    // A held token is worse than briefly stopping a running device.
    const seen = []
    mockFetch(async (url) => {
      seen.push(String(url))
      if (String(url).includes('/devices')) throw new Error('sidecar went away')
      return ok({})
    })

    const out = await sidecar.releasePushIfIdle()

    expect(out.stopped).toBe(true)
    expect(out.devices).toBeNull()
    expect(seen.some(u => u.includes('/push/stop'))).toBe(true)
  })

  it('returns the list it decided from, so the caller need not read it twice', async () => {
    const list = [{ device_id: 'camera', kind: 'face', running: true }]
    mockFetch(async (url) =>
      String(url).includes('/devices') ? devicesReply(list) : ok({}))

    const out = await sidecar.releasePushIfIdle()

    expect(out.devices).toEqual(list)
  })
})

describe('sidecarDebug', () => {
  // `available` reflects whether calls actually reached the sidecar.

  const stateReply = (data) => ok({ status: 'ok', data })

  it('reports a sidecar that answered nothing yet as available', async () => {
    // `data: null` is normal with no stream yet, not unreachability.
    mockFetch(async (url) =>
      String(url).includes('/muse/status') ? stateReply(null) : stateReply(null))

    const out = await sidecar.sidecarDebug('default')

    expect(out.available).toBe(true)
    expect(out.snapshot).toBeNull()
    expect(out.muse).toEqual({})
  })

  it('reports a sidecar that did not answer as unavailable', async () => {
    mockFetch(async () => { throw new Error('ECONNREFUSED') })

    const out = await sidecar.sidecarDebug('default')

    expect(out.available).toBe(false)
    expect(out.snapshot).toBeNull()
    expect(out.muse).toBeNull()
  })

  it('stays available when only one route is unhappy', async () => {
    // A half-working sidecar still has something to show.
    mockFetch(async (url) => {
      if (String(url).includes('/muse/status')) throw new Error('no muse route')
      return stateReply({ state: { focus: 42 } })
    })

    const out = await sidecar.sidecarDebug('default')

    expect(out.available).toBe(true)
    expect(out.snapshot).toEqual({ state: { focus: 42 } })
    expect(out.muse).toBeNull()
  })

  it('never throws, so the panel keeps a payload to branch on', async () => {
    // A throw would null `eegDebug` and show the pull-mode outage message.
    mockFetch(async () => { throw new Error('boom') })

    await expect(sidecar.sidecarDebug('default')).resolves.toMatchObject({
      ingest_mode: 'push',
    })
  })
})
