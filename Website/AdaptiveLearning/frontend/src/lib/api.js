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

export async function apiFetch(path, { method = 'GET', body = null } = {}) {
  const token = await getAccessToken()
  // console.log("TOKEN:", token)
  const headers = { 'Content-Type': 'application/json' }
  if (token) headers.Authorization = `Bearer ${token}`

  const opts = { method, headers }
  if (body) opts.body = JSON.stringify(body)

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