import { supabase } from './supabase'

const API_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000'

async function getAccessToken() {
  // supabase v2: supabase.auth.getSession()
  try {
    const { data } = await supabase.auth.getSession()
    return data?.session?.access_token || null
  } catch (e) {
    return null
  }
}

/** `timeoutMs` is opt-in, and deliberately has no default.
 *
 * Most callers here render into a page that is already on screen, so a slow
 * request costs a spinner in one panel and a blanket default would be a new way
 * for them to fail. The one that cannot afford to wait for ever is the role
 * read in `AuthContext`, because `loading` gates every route: a request that
 * *hangs* rather than fails leaves the whole app behind a loader with nothing
 * to catch. Callers that gate something pass a bound; the rest do not.
 *
 * Not a blanket default for a second reason: `/api/students/{id}/learning-
 * strategies` is bounded server-side at `STRATEGY_LLM_TIMEOUT` (20s) and can
 * queue behind other waiters first, so any default short enough to be useful
 * here would abort that one mid-flight -- and `sidecar.js` already documents
 * what a client timeout shorter than the work does: it does not cancel
 * anything, it just stops you finding out what happened.
 *
 * The bound covers the **whole call**, not just the fetch. `getAccessToken`
 * awaits `supabase.auth.getSession()`, which goes to the network when the
 * access token needs refreshing -- so a timeout wrapped around `fetch` alone
 * would leave exactly the hang it was added to stop.
 */
export async function apiFetch(path, { method = 'GET', body = null,
                                       timeoutMs = null } = {}) {
  if (!timeoutMs) return request(path, { method, body })

  const controller = new AbortController()
  let timer
  const expired = new Promise((_, reject) => {
    timer = setTimeout(() => {
      // Aborted as well as rejected: without this the request stays in flight
      // after the caller has given up on it.
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
  const token = await getAccessToken()
  const headers = { 'Content-Type': 'application/json' }
  if (token) headers.Authorization = `Bearer ${token}`

  const opts = { method, headers }
  if (body) opts.body = JSON.stringify(body)
  if (signal) opts.signal = signal

  const res = await fetch(`${API_URL}${path}`, opts)
  if (!res.ok) {
    const txt = await res.text()
    let detail = txt
    try { detail = JSON.parse(txt) } catch {}
    const err = new Error(detail?.detail || detail || res.statusText)
    // Callers that must tell "this doesn't exist" apart from "the request
    // failed" need the code, and the message alone doesn't carry it.
    err.status = res.status
    throw err
  }
  return res.json()
}