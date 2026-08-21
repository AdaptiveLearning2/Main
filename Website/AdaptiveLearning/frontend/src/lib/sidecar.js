/**
 * The browser's direct line to the EEG sidecar on this machine.
 *
 * Under push ingestion the sidecar runs on the student's own laptop, which a
 * hosted backend can't reach — so the page calls `http://127.0.0.1:8001`
 * directly instead of going through the backend.
 *
 * An HTTPS page is allowed to call loopback HTTP like this (verified on
 * Chromium; details in `EEGResearch/docs/LOOPBACK_FROM_HTTPS.md`).
 *
 * `VITE_EEG_LOCAL_TOKEN` being in the client bundle is fine: the sidecar only
 * binds to loopback, so the token just separates this page from other pages
 * in the same browser, not one user from another. It is not the student's
 * real backend credential — that one is fetched per call from the Supabase
 * session and handed to the sidecar so it can post as the student.
 */

import { supabase } from './supabase'

const SIDECAR_URL = import.meta.env.VITE_EEG_LOCAL_URL || 'http://127.0.0.1:8001'
const SIDECAR_TOKEN = import.meta.env.VITE_EEG_LOCAL_TOKEN || ''

/** Short, since this is a same-machine process — an absent sidecar should
 *  fail fast rather than stall the page. */
const TIMEOUT_MS = 3000

/** Starting a device needs longer: opening a webcam and loading FER+/the
 * face landmarker takes seconds, and the muse path waits on a BLE bridge. A
 * short timeout here would abort the browser's request while the sidecar
 * kept going and started the device anyway — leaving the UI showing "off"
 * while the camera was actually running.
 */
const LIFECYCLE_TIMEOUT_MS = 30000

async function call(path, { method = 'GET', body = null,
                            timeoutMs = TIMEOUT_MS } = {}) {
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), timeoutMs)
  try {
    const res = await fetch(`${SIDECAR_URL}${path}`, {
      method,
      headers: {
        'Content-Type': 'application/json',
        ...(SIDECAR_TOKEN ? { Authorization: `Bearer ${SIDECAR_TOKEN}` } : {}),
      },
      body: body ? JSON.stringify(body) : undefined,
      signal: controller.signal,
    })
    const text = await res.text()
    let parsed = text
    try { parsed = JSON.parse(text) } catch { /* not JSON, keep raw text */ }
    if (!res.ok) {
      const err = new Error(parsed?.detail || parsed || res.statusText)
      err.status = res.status
      throw err
    }
    return parsed
  } finally {
    clearTimeout(timer)
  }
}

/** Whether a sidecar is answering on this machine at all. */
export async function sidecarAlive() {
  try {
    await call('/healthz')
    return true
  } catch {
    return false
  }
}

/**
 * Hand the sidecar this session and the student's own backend token.
 *
 * The token lets the sidecar post as the student; it lives only in that
 * process's memory. Calling this again for the same session replaces the
 * token in place without touching the queue (what a token refresh needs).
 * Calling it for a different session drops the old queue, since those
 * samples belong to a session this token may not own.
 */
export async function startPush(sessionId, accessTokenOverride = null) {
  // `accessTokenOverride` is for the `onAuthStateChange` handler, which is
  // handed the new session directly and must not call `getSession()` itself:
  // supabase-js v2 holds an auth lock during that callback, so awaiting
  // `getSession()` inside it deadlocks.
  let accessToken = accessTokenOverride
  if (!accessToken) {
    const { data } = await supabase.auth.getSession()
    accessToken = data?.session?.access_token
  }
  if (!accessToken) throw new Error('Not signed in')
  return call('/api/v1/push/start', {
    method: 'POST',
    body: { session_id: sessionId, access_token: accessToken },
  })
}

/** Stop pushing, flush the tail, and drop the token. */
export async function stopPush() {
  return call('/api/v1/push/stop', { method: 'POST' })
}

/**
 * The same stop, issued from a page that is going away.
 *
 * React effect cleanup does not run on tab close, hard refresh, or
 * navigation away, so `stopPush` never fires for the most common way a
 * student ends a lesson. Without this the sidecar keeps the token and keeps
 * recording until it expires — a consent problem, not just untidiness.
 *
 * `keepalive` lets the request outlive the document. `sendBeacon` can't be
 * used instead because it can't set an `Authorization` header.
 */
export function stopPushOnUnload() {
  try {
    return fetch(`${SIDECAR_URL}/api/v1/push/stop`, {
      method: 'POST',
      headers: SIDECAR_TOKEN ? { Authorization: `Bearer ${SIDECAR_TOKEN}` } : {},
      keepalive: true,
    }).catch(() => {})
  } catch {
    // Page is unloading; there's nothing more useful to do.
    return Promise.resolve()
  }
}

/**
 * Queue depths and delivery counts.
 *
 * `recorded` is what the backend actually stored, not what was sent — it
 * drops samples for a sensor the student declined. Counting sent samples
 * instead would show a healthy session that recorded nothing.
 */
export async function pushStatus() {
  const out = await call('/api/v1/push/status')
  return out?.data || out
}

// ── driving the local hardware, push only ───────────────────────────────────
//
// Under `pull`, the backend owns this by polling the sidecar and proxying
// scan/connect through `/api/eeg/muse/*`. Under push the backend is remote
// and can't reach the student's hardware, so those backend endpoints refuse
// (409) and the page talks to the sidecar directly instead. The sidecar only
// accepts the learner token here when `PUSH_ENABLED` is on; under pull it
// answers 401. So everything below is push-only — `Adaptive.jsx` must not
// call it otherwise.

/** Start capturing on one registered device (`default`, `camera`, ...). */
export async function deviceStart(deviceId) {
  return call(`/api/v1/session/start?device_id=${encodeURIComponent(deviceId)}`,
              { method: 'POST', timeoutMs: LIFECYCLE_TIMEOUT_MS })
}

/** Stop capturing on one device. Leaves any other device running. */
export async function deviceStop(deviceId) {
  return call(`/api/v1/session/stop?device_id=${encodeURIComponent(deviceId)}`,
              { method: 'POST', timeoutMs: LIFECYCLE_TIMEOUT_MS })
}

/** Every registered station and whether it is currently capturing. */
export async function devices() {
  const res = await call('/api/v1/devices')
  return res?.data || []
}

/** Ask the native bridge to scan for nearby headbands. */
export async function museRefresh(deviceId) {
  return call(`/api/v1/muse/refresh?device_id=${encodeURIComponent(deviceId)}`,
              { method: 'POST', timeoutMs: LIFECYCLE_TIMEOUT_MS })
}

/** Pair with a named headband. `name` comes from the scan's device list. */
export async function museConnect(name, deviceId) {
  return call('/api/v1/muse/connect',
              { method: 'POST', body: { name, device_id: deviceId },
                timeoutMs: LIFECYCLE_TIMEOUT_MS })
}

export async function museDisconnect(deviceId) {
  return call(`/api/v1/muse/disconnect?device_id=${encodeURIComponent(deviceId)}`,
              { method: 'POST', timeoutMs: LIFECYCLE_TIMEOUT_MS })
}

/** One device's snapshot, unwrapped to `{running, ingestion, ...}`.
 *
 * Matches the shape the backend's `/api/eeg/status` puts under its `muse`
 * key, so a caller reading `ingestion.muse_devices` sees the same shape in
 * both modes regardless of which function it called.
 */
export async function museState(deviceId) {
  const res = await call(`/api/v1/muse/status?device_id=${encodeURIComponent(deviceId)}`)
  return res?.data || {}
}

/** Tear down the shared push client, but only once nothing is left using it.
 *
 * `/api/v1/push/stop` is global — one `push_client` serves every device, so
 * stopping it unconditionally when either sensor turns off breaks the other:
 * stopping on headband-off silently kills a running camera's delivery, and
 * never stopping on camera-off leaves the sidecar holding the student's
 * token after they've walked away.
 *
 * Returns the device list it decided from, so a caller can update its own
 * view from the same read instead of a second one that could disagree.
 */
export async function releasePushIfIdle() {
  let list = null
  try {
    list = await devices()
  } catch {
    // Couldn't tell what's running, so stop it — a token left behind is
    // worse than a device that briefly reports itself as not running.
    await stopPush().catch(() => {})
    return { stopped: true, devices: null }
  }
  if (list.some(d => d.running)) return { stopped: false, devices: list }
  await stopPush().catch(() => {})
  return { stopped: true, devices: list }
}

/** The interpreted EEG snapshot: state, features, bands, ingestion.
 *
 * Returns null when no stream data has arrived yet — a normal state, not a
 * failure, so it doesn't throw.
 */
export async function sidecarState(deviceId) {
  const res = await call(`/api/v1/state?device_id=${encodeURIComponent(deviceId)}`)
  return res?.data || null
}

/** What `/api/eeg/debug` returns under pull, assembled here from the sidecar
 * directly since the backend can't reach the student's laptop under push.
 *
 * `available` is tracked separately from what each call returned, because
 * a normal empty response (no stream data yet, no headband attached) looks
 * the same as a call that never reached the sidecar. Deriving `available`
 * from the payload shape would misreport a healthy-but-idle sidecar as down.
 */
export async function sidecarDebug(deviceId) {
  const [state, muse] = await Promise.allSettled([
    sidecarState(deviceId),
    museState(deviceId),
  ])
  return {
    // Either route answering means the sidecar is up.
    available:   state.status === 'fulfilled' || muse.status === 'fulfilled',
    ingest_mode: 'push',
    snapshot:    state.status === 'fulfilled' ? state.value : null,
    muse:        muse.status === 'fulfilled' ? muse.value : null,
  }
}
