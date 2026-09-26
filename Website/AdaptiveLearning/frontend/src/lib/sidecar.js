/**
 * The browser's direct line to the EEG sidecar on this machine (push mode,
 * where a hosted backend can't reach the student's laptop).
 * `VITE_EEG_LOCAL_TOKEN` in the bundle is fine: the sidecar binds to loopback only.
 * See `EEGResearch/docs/LOOPBACK_FROM_HTTPS.md`.
 */

import { supabase } from './supabase'
import { DEFAULT_SIDECAR_URL } from './origins'

const SIDECAR_URL = import.meta.env.VITE_EEG_LOCAL_URL || DEFAULT_SIDECAR_URL
const SIDECAR_TOKEN = import.meta.env.VITE_EEG_LOCAL_TOKEN || ''

/** Same-machine process: an absent sidecar should fail fast. */
const TIMEOUT_MS = 3000

/** Device start takes seconds; timing out early leaves the UI "off" while the device runs. */
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
 * Hand the sidecar this session and the student's backend token.
 * Same session: replaces the token, keeps the queue. New session: drops the queue.
 */
export async function startPush(sessionId, accessTokenOverride = null) {
  // For `onAuthStateChange`: awaiting `getSession()` inside it deadlocks.
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
 * `stopPush` for a page that is going away (effect cleanup doesn't run on unload).
 * `keepalive`, not `sendBeacon`, which can't set `Authorization`.
 */
export function stopPushOnUnload() {
  try {
    return fetch(`${SIDECAR_URL}/api/v1/push/stop`, {
      method: 'POST',
      headers: SIDECAR_TOKEN ? { Authorization: `Bearer ${SIDECAR_TOKEN}` } : {},
      keepalive: true,
    }).catch(() => {})
  } catch {
    return Promise.resolve()
  }
}

/**
 * Queue depths and delivery counts.
 * `recorded` is what the backend stored, not what was sent.
 */
export async function pushStatus() {
  const out = await call('/api/v1/push/status')
  return out?.data || out
}

// ── driving the local hardware, push only ───────────────────────────────────
// Under pull the backend proxies these (`/api/eeg/muse/*`) and the sidecar
// answers 401 here, so `Adaptive.jsx` must not call them otherwise.

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

/** `deviceStop` for a page that is going away; `stopPushOnUnload` alone leaves the camera open. */
export function deviceStopOnUnload(deviceId) {
  try {
    return fetch(`${SIDECAR_URL}/api/v1/session/stop?device_id=${encodeURIComponent(deviceId)}`, {
      method: 'POST',
      headers: SIDECAR_TOKEN ? { Authorization: `Bearer ${SIDECAR_TOKEN}` } : {},
      keepalive: true,
    }).catch(() => {})
  } catch {
    return Promise.resolve()
  }
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

/** One device's snapshot, the same shape as `/api/eeg/status`'s `muse` key. */
export async function museState(deviceId) {
  const res = await call(`/api/v1/muse/status?device_id=${encodeURIComponent(deviceId)}`)
  return res?.data || {}
}

/**
 * Stop the shared (global) push client once no device is running.
 * Returns the device list it decided from, so the caller need not re-read.
 */
export async function releasePushIfIdle() {
  let list = null
  try {
    list = await devices()
  } catch {
    // Unknown: stop anyway; a token left behind is worse.
    await stopPush().catch(() => {})
    return { stopped: true, devices: null }
  }
  if (list.some(d => d.running)) return { stopped: false, devices: list }
  await stopPush().catch(() => {})
  return { stopped: true, devices: list }
}

/** The interpreted EEG snapshot, or null (not a throw) before any stream data. */
export async function sidecarState(deviceId) {
  const res = await call(`/api/v1/state?device_id=${encodeURIComponent(deviceId)}`)
  return res?.data || null
}

/**
 * The push-mode equivalent of `/api/eeg/debug`.
 * `available` comes from call status, not payload shape: an idle sidecar returns empty.
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
