/**
 * The browser's direct line to the EEG sidecar on this machine.
 *
 * Under push ingestion the sidecar is a per-student process on the student's own
 * laptop, and a hosted backend has no route to it. Lifecycle control therefore
 * cannot go through the backend the way it does in the co-located deployment:
 * the page calls `http://127.0.0.1:8001` itself.
 *
 * **An HTTPS page may do this**, measured on Chromium 148 with a negative
 * control — plain HTTP to a non-loopback host from the same page is blocked and
 * loopback is not, so the exemption is real and specific rather than the browser
 * being lax. Evidence and the limits (other browsers; Private Network Access, a
 * scheduled change that would show up as the preflight failing) are in
 * `EEGResearch/docs/LOOPBACK_FROM_HTTPS.md`.
 *
 * **`VITE_EEG_LOCAL_TOKEN` is in the client bundle and that is fine here.** The
 * sidecar binds to loopback only, so the token is useless from any other
 * machine — it separates this page from other pages in this browser, not one
 * user from another. Saying so plainly beats shipping it while implying it is a
 * secret. It is *not* the student's backend credential: that one is a real
 * secret, is fetched per call from the Supabase session, and is handed to the
 * sidecar once so it can post as the student.
 */

import { supabase } from './supabase'

const SIDECAR_URL = import.meta.env.VITE_EEG_LOCAL_URL || 'http://127.0.0.1:8001'
const SIDECAR_TOKEN = import.meta.env.VITE_EEG_LOCAL_TOKEN || ''

/** Short, because this is a process on the same machine. A sidecar that is not
 *  running should read as "not there" in a moment, not stall a page load. */
const TIMEOUT_MS = 3000

/** Starting a device is not a status read.
 *
 * Opening a webcam and loading FER+ and the face landmarker takes seconds, and
 * the muse path waits on a BLE bridge. At 3 s the browser aborted while the
 * sidecar carried on and started the device anyway -- so the request "failed",
 * the card stayed on "off", and the camera was running the whole time. A
 * client-side timeout shorter than the work it is waiting for does not cancel
 * anything; it just stops you finding out what happened.
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
    try { parsed = JSON.parse(text) } catch { /* keep the text */ }
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
 * The token is what lets the sidecar post as the student; it lives in that
 * process's memory for the session and is never written to disk. Calling this
 * again for the *same* session replaces the token without disturbing the queue,
 * which is exactly what a token refresh needs — see the refresh handler in
 * `Adaptive.jsx`. Calling it for a different session drops the old queue,
 * because those samples belong to a session this token may not own.
 */
export async function startPush(sessionId, accessTokenOverride = null) {
  // `accessTokenOverride` exists for one caller: the `onAuthStateChange`
  // handler, which is *given* the new session and must not ask for it.
  // supabase-js v2 holds an internal auth lock while dispatching that callback,
  // and `getSession()` waits on the same lock -- so awaiting it from inside the
  // callback deadlocks. The refresh handler would hang forever, the sidecar
  // would keep the expired token, and every push would 401 for the rest of the
  // lesson with nothing raised anywhere.
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
 * React effect cleanup does not run on a tab close, a hard refresh, or a
 * navigation away, so the ordinary `stopPush` never fires for the most common
 * way a student ends a lesson. The sidecar would keep the bearer token and keep
 * posting until the token expired -- up to an hour of recording after the
 * student thought they were done, which is a consent problem rather than a
 * tidiness one.
 *
 * `keepalive` is what makes it work: the browser lets the request outlive the
 * document. `sendBeacon` is the usual tool here and is no use, because it
 * cannot set an `Authorization` header.
 */
export function stopPushOnUnload() {
  try {
    return fetch(`${SIDECAR_URL}/api/v1/push/stop`, {
      method: 'POST',
      headers: SIDECAR_TOKEN ? { Authorization: `Bearer ${SIDECAR_TOKEN}` } : {},
      keepalive: true,
    }).catch(() => {})
  } catch {
    // Nothing useful to do from a page that is unloading.
    return Promise.resolve()
  }
}

/**
 * Queue depths and delivery counts.
 *
 * `recorded` is what the backend said it *stored*, not what was sent — it drops
 * samples for a sensor the student declined. A surface reporting sent counts
 * would show a healthy session that recorded nothing.
 */
export async function pushStatus() {
  const out = await call('/api/v1/push/status')
  return out?.data || out
}

// ── driving the local hardware, push only ───────────────────────────────────
//
// Under `pull` the backend owns this: it polls the sidecar and proxies scan and
// connect through `/api/eeg/muse/*`. Push inverts it. The backend is remote by
// definition there -- that is why push exists -- so those endpoints answer 409
// (`_refuse_under_push`), and the page in front of the student is the only
// thing that can reach their headband and their camera.
//
// The sidecar accepts the learner token for these only when PUSH_ENABLED is on
// (see `require_local_controller`); under pull it still answers 401, which is
// correct rather than a limitation. So every function here is for the push path
// and Adaptive.jsx must not call them otherwise.

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
 * Deliberately the same shape the backend's `/api/eeg/status` puts under its
 * `muse` key, so a caller polling for `ingestion.muse_devices` reads one shape
 * in both modes. The sidecar wraps it in `{status, data}`; unwrapping here
 * rather than at the call site keeps the mode difference to which function is
 * called, not what the answer looks like.
 */
export async function museState(deviceId) {
  const res = await call(`/api/v1/muse/status?device_id=${encodeURIComponent(deviceId)}`)
  return res?.data || {}
}

/** Tear down the shared push client, but only once nothing is left using it.
 *
 * `/api/v1/push/stop` is **global**: there is one `push_client` for the whole
 * sidecar, and stopping it clears `set_payload_consumer` for every device at
 * once. So "whoever disconnects last, unconditionally" is wrong in both
 * directions, and both were wrong:
 *
 * - the headband's off-path called `stopPush()` outright, which silently killed
 *   a running camera's delivery. Its frames went nowhere while the card still
 *   said RECORDING, because nothing told it.
 * - the camera's off-path never called it at all, so turning the camera off
 *   with no headband left the sidecar holding the student's access token --
 *   a consent problem, not untidiness.
 *
 * Returns the device list it decided from, so a caller can update what it shows
 * from the same read rather than a second one that could disagree.
 */
export async function releasePushIfIdle() {
  let list = null
  try {
    list = await devices()
  } catch {
    // Could not tell. Stop it: a sidecar holding a token after the student
    // walked away is the worse of the two failures, and a device still
    // capturing will report `running: false` on the next poll rather than
    // claiming to record into a client that is gone.
    await stopPush().catch(() => {})
    return { stopped: true, devices: null }
  }
  if (list.some(d => d.running)) return { stopped: false, devices: list }
  await stopPush().catch(() => {})
  return { stopped: true, devices: list }
}

/** The interpreted EEG snapshot: state, features, bands, ingestion.
 *
 * `Envelope`-wrapped by the sidecar, and `status: "idle"` with `data: null`
 * before any stream data has arrived -- which is a real state, not a failure,
 * so it comes back as null rather than throwing.
 */
export async function sidecarState(deviceId) {
  const res = await call(`/api/v1/state?device_id=${encodeURIComponent(deviceId)}`)
  return res?.data || null
}

/** What `/api/eeg/debug` returns under pull, assembled from the sidecar.
 *
 * The debug panel was written against the backend's proxy, and under push the
 * backend has nothing to proxy -- it cannot reach a student's laptop. That made
 * the panel say so and stop, which was honest but left push with no way to see
 * focus, calm or contact quality at all. The page can reach the sidecar, so the
 * same payload is assembled here rather than the panel learning a second shape.
 */
export async function sidecarDebug(deviceId) {
  const [snapshot, muse] = await Promise.all([
    sidecarState(deviceId).catch(() => null),
    museState(deviceId).catch(() => ({})),
  ])
  return { available: true, ingest_mode: 'push', snapshot, muse }
}
