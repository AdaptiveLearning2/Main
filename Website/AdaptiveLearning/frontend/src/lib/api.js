import { supabase } from './supabase'
import { startRequest } from './serverWake'

const API_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000'

// The last token a request carried, for a page that is going away and cannot await one.
let lastToken = null

/** The current access token and the account it belongs to; both null when signed out or unreadable. */
async function readSession() {
  try {
    const { data } = await supabase.auth.getSession()
    lastToken = data?.session?.access_token || null
    return { token: lastToken, account: (lastToken && data.session.user?.id) || null }
  } catch {
    return { token: null, account: null }
  }
}

async function getAccessToken() {
  return (await readSession()).token
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

// How long `cache: true` reuses a read that changes only when someone writes it.
const SHORT_CACHE_MS = 30_000

// Opted-in GETs, keyed by account and path: { path, promise } in flight, then { path, data, expiresAt }.
const readCache = new Map()
const cacheKey = (account, path) => JSON.stringify([account, path])

// "/api/classes/c1/join-code" -> "/api/classes": a write there invalidates every read under it.
const resourceOf = (path) => path.split('?')[0].split('/').slice(0, 3).join('/')

function invalidate(path) {
  const resource = resourceOf(path)
  for (const [key, entry] of [...readCache]) if (resourceOf(entry.path) === resource) readCache.delete(key)
}

/** Drop every cached read: on sign-out, so a shared machine's next account sees none of them. */
export function clearApiCache() {
  readCache.clear()
}

/**
 * `timeoutMs` is opt-in with no default (slow endpoints like strategies are legitimate).
 * It bounds the whole call, token refresh included, not just `fetch`. `cache` (GET only)
 * reuses a result, or a request in flight, for 30 s (`cacheMs` to choose); failures never.
 * A cached result is per account, and every caller gets its own copy.
 */
export async function apiFetch(path, { method = 'GET', body = null,
                                       timeoutMs = null, cache = false,
                                       cacheMs = cache ? SHORT_CACHE_MS : null } = {}) {
  if (method !== 'GET') {
    // Before and after: a read racing the write must not repopulate what it changed.
    invalidate(path)
    try { return await bounded(path, { method, body, timeoutMs }) } finally { invalidate(path) }
  }
  if (!cacheMs) return bounded(path, { method, body, timeoutMs })
  // Each caller bounds only its own wait; giving up releases its interest in the shared request.
  const waiter = { gaveUp: false, release: () => {} }
  const data = await within(path, timeoutMs, cachedRead(path, cacheMs, waiter),
                            () => { waiter.gaveUp = true; waiter.release() })
  return structuredClone(data)
}

async function cachedRead(path, cacheMs, waiter) {
  // The token sent is the token whose account keys the entry, so no entry answers another account.
  const { token, account } = await readSession()
  if (waiter.gaveUp) return null      // timed out reading the session: join nothing
  const key = cacheKey(account, path)
  const hit = readCache.get(key)
  if (hit && !hit.promise && hit.expiresAt > Date.now()) return hit.data
  const entry = hit?.promise ? hit : startSharedRead(key, path, cacheMs, token)
  entry.waiters++
  waiter.release = () => {
    // Only the last waiter to give up aborts it, and drops it so the next caller starts afresh.
    if (--entry.waiters > 0 || entry.settled) return
    entry.controller.abort()
    if (readCache.get(key) === entry) readCache.delete(key)
  }
  return entry.promise
}

/** A cached GET with no bound of its own: its waiters' bounds decide when it is abandoned. */
function startSharedRead(key, path, cacheMs, token) {
  const entry = { path, waiters: 0, settled: false, controller: new AbortController() }
  entry.promise = request(path, { method: 'GET', body: null, token, signal: entry.controller.signal }).then(
    data => {
      entry.settled = true
      // Invalidated meanwhile: the write wins, so this result is not kept.
      if (readCache.get(key) === entry) readCache.set(key, { path, data, expiresAt: Date.now() + cacheMs })
      return data
    },
    err => {
      entry.settled = true
      if (readCache.get(key) === entry) readCache.delete(key)
      throw err
    })
  readCache.set(key, entry)
  return entry
}

/** Rejects with a `timeout` error once `timeoutMs` passes, calling `onExpire` first; none: no bound. */
function within(path, timeoutMs, promise, onExpire) {
  if (!timeoutMs) return promise
  let timer
  const expired = new Promise((_, reject) => {
    timer = setTimeout(() => {
      onExpire?.()
      const err = new Error(`Request to ${path} timed out after ${timeoutMs}ms`)
      err.timeout = true
      reject(err)
    }, timeoutMs)
  })
  return Promise.race([promise, expired]).finally(() => clearTimeout(timer))
}

async function bounded(path, { method, body, timeoutMs }) {
  if (!timeoutMs) return request(path, { method, body })
  const controller = new AbortController()
  // Abort the request too, or it keeps running after the caller gives up.
  return within(path, timeoutMs, request(path, { method, body, signal: controller.signal }),
                () => controller.abort())
}

async function request(path, { method, body, signal, token }) {
  for (let attempt = 0; ; attempt++) {
    const res = await send(path, { method, body, signal, token })
    // 503 + `Retry-After` is a pause, not a failure. GET only, so no side effect replays.
    const delay = res.status === 503 && method === 'GET' && attempt < RETRY_ATTEMPTS
      ? retryAfterMs(res)
      : null
    if (delay === null) return finish(res)
    await sleep(jittered(delay), signal)
  }
}

// `token` undefined: read the session now. A cached read passes the one its key was made from.
async function send(path, { method, body, signal, token: given }) {
  const token = given === undefined ? await getAccessToken() : given
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
    try { detail = JSON.parse(txt) } catch { /* not JSON: the text is the detail */ }
    const err = new Error(detail?.detail || detail || res.statusText)
    err.status = res.status
    throw err
  }
  return res.json()
}