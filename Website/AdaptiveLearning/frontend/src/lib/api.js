import { supabase } from './supabase'
import { startRequest } from './serverWake'

const API_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000'

// The last token a request carried, for a page that is going away and cannot await one.
let lastToken = null

async function getAccessToken() {
  try {
    const { data } = await supabase.auth.getSession()
    lastToken = data?.session?.access_token || null
    return lastToken
  } catch {
    return null
  }
}

/**
 * A POST that outlives the document: `keepalive`, sent synchronously with the last token seen.
 * For `pagehide`, where `apiFetch`'s awaited `getSession()` never finishes. Never throws;
 * sends nothing with no token, since the backend would only answer 401.
 */
export function apiFetchOnUnload(path, body) {
  if (!lastToken) return
  try {
    fetch(`${API_URL}${path}`, {
      method: 'POST', keepalive: true, body: JSON.stringify(body),
      headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${lastToken}` },
    }).catch(() => {})
  } catch {
    // Nothing to do on the way out.
  }
}

/** Retries for a 503 with `Retry-After`, and the longest delay honoured (a wrong value must not park the UI). */
const RETRY_ATTEMPTS = 2
const MAX_RETRY_DELAY_MS = 10_000

/** `Retry-After` in ms (delay-seconds or HTTP-date), clamped, or null. */
function retryAfterMs(res) {
  const raw = res.headers?.get?.('Retry-After')
  if (!raw) return null
  const seconds = Number(raw)
  const ms = Number.isFinite(seconds)
    ? seconds * 1000
    : Date.parse(raw) - Date.now()
  if (!Number.isFinite(ms)) return null
  return Math.min(Math.max(ms, 0), MAX_RETRY_DELAY_MS)
}

/** Full jitter over [0, ms], so a refused class does not retry in lockstep. */
function jittered(ms) {
  return Math.random() * ms
}

const sleep = (ms, signal) => new Promise(resolve => {
  const timer = setTimeout(resolve, ms)
  // Abort ends the wait: the caller's timeout has already rejected.
  signal?.addEventListener?.('abort', () => { clearTimeout(timer); resolve() },
                             { once: true })
})

/**
 * `timeoutMs` is opt-in with no default (slow endpoints like strategies are legitimate).
 * It bounds the whole call, token refresh included, not just `fetch`.
 */
export async function apiFetch(path, { method = 'GET', body = null,
                                       timeoutMs = null } = {}) {
  if (!timeoutMs) return request(path, { method, body })

  const controller = new AbortController()
  let timer
  const expired = new Promise((_, reject) => {
    timer = setTimeout(() => {
      // Abort the request too, or it keeps running after the caller gives up.
      controller.abort()
      const err = new Error(`Request to ${path} timed out after ${timeoutMs}ms`)
      err.timeout = true
      reject(err)
    }, timeoutMs)
  })

  try {
    return await Promise.race([
      request(path, { method, body, signal: controller.signal }),
      expired,
    ])
  } finally {
    clearTimeout(timer)
  }
}

async function request(path, { method, body, signal }) {
  for (let attempt = 0; ; attempt++) {
    const res = await send(path, { method, body, signal })
    // 503 + `Retry-After` is a pause, not a failure. GET only, so no side effect replays.
    const delay = res.status === 503 && method === 'GET' && attempt < RETRY_ATTEMPTS
      ? retryAfterMs(res)
      : null
    if (delay === null) return finish(res)
    await sleep(jittered(delay), signal)
  }
}

async function send(path, { method, body, signal }) {
  const token = await getAccessToken()
  const headers = { 'Content-Type': 'application/json' }
  if (token) headers.Authorization = `Bearer ${token}`

  const opts = { method, headers }
  if (body) opts.body = JSON.stringify(body)
  if (signal) opts.signal = signal

  // Any HTTP status is an answer: a server that refuses is awake. A network error is not.
  const done = startRequest()
  try {
    const res = await fetch(`${API_URL}${path}`, opts)
    done(true)
    return res
  } catch (e) {
    done(false)
    throw e
  }
}

async function finish(res) {
  if (!res.ok) {
    const txt = await res.text()
    let detail = txt
    try { detail = JSON.parse(txt) } catch {}
    const err = new Error(detail?.detail || detail || res.statusText)
    err.status = res.status
    throw err
  }
  return res.json()
}