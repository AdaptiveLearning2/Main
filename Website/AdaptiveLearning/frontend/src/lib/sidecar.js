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

async function call(path, { method = 'GET', body = null } = {}) {
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), TIMEOUT_MS)
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
export async function startPush(sessionId) {
  const { data } = await supabase.auth.getSession()
  const accessToken = data?.session?.access_token
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
