import { supabase } from './supabase'

const API_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000'

async function getAccessToken() {
  try {
    const { data } = await supabase.auth.getSession()
    return data?.session?.access_token || null
  } catch {
    return null
  }
}

/** How many times a refusal that asked us to come back is retried, and the
 * longest we will honour. The server's `Retry-After` is a request, not an
 * instruction: a wrong or hostile value must not park the UI for an hour.
 */
const RETRY_ATTEMPTS = 2
const MAX_RETRY_DELAY_MS = 10_000

/** The delay the server asked for, in ms, or null if it asked for nothing.
 *
 * RFC 9110 allows both forms — delay-seconds and an HTTP-date — and the
 * backend sends the first. The second is parsed anyway because it costs two
 * lines and a caching proxy may rewrite one into the other.
 */
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

/** Wait, but not in lockstep with everyone else who was refused.
 *
 * This is the part that is easy to leave out and defeats the whole change.
 * The 503 being retried is raised when a *class* starts together, so every
 * refused browser is holding the same `Retry-After: 5` and would come back
 * at the same instant — reforming the burst that caused the refusal, one
 * round later. The jitter spreads them, and measurably matters: the same
 * thirty students arriving over 10s are served 87% where a simultaneous
 * arrival is served 40%.
 *
 * Full jitter over [0, delay] rather than delay + a wobble: the point is to
 * decorrelate, and a tight band around a common centre is still a herd.
 */
function jittered(ms) {
  return Math.random() * ms
}

const sleep = (ms, signal) => new Promise(resolve => {
  const timer = setTimeout(resolve, ms)
  // Abort ends the wait immediately: the caller's `timeoutMs` has expired and
  // the race has already rejected, so sitting here would keep a timer alive
  // past the point anyone is listening.
  signal?.addEventListener?.('abort', () => { clearTimeout(timer); resolve() },
                             { once: true })
})

/** `timeoutMs` is opt-in with no default. Most callers show a spinner on a
 * slow request, so a blanket timeout would just add a new failure mode. The
 * exception is a caller that gates a whole page behind the request (like the
 * role read in `AuthContext`), which must not hang forever with `loading`
 * stuck true.
 *
 * A blanket default would also be too short for the LLM strategies endpoint,
 * which can legitimately take up to `STRATEGY_LLM_TIMEOUT` (20s) server-side.
 *
 * The bound covers the whole call, not just `fetch` — `getAccessToken` can
 * itself hit the network refreshing the token, so wrapping only `fetch`
 * would leave that part unbounded.
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
    // A 503 carrying `Retry-After` is the server saying "a ceiling is full,
    // come back" — a pause, not a failure, and until this existed the header
    // the backend has always sent was read by nothing: a refused student saw
    // an error screen. Retried only for GET, so this can never replay a
    // side effect; both generation endpoints are GETs, which is what makes
    // that restriction free rather than a compromise.
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

  return fetch(`${API_URL}${path}`, opts)
}

async function finish(res) {
  if (!res.ok) {
    const txt = await res.text()
    let detail = txt
    try { detail = JSON.parse(txt) } catch {}
    const err = new Error(detail?.detail || detail || res.statusText)
    // Carry the status code so callers can tell errors apart (e.g. 404 vs 500).
    err.status = res.status
    throw err
  }
  return res.json()
}