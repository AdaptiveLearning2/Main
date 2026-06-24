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
    throw new Error(detail?.detail || detail || res.statusText)
  }
  return res.json()
}