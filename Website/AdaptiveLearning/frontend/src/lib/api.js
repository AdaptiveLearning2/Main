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
    // Carry the status code so callers can tell errors apart (e.g. 404 vs 500).
    err.status = res.status
    throw err
  }
  return res.json()
}