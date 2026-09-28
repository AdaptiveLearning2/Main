/** The real auth client never adopts a session from a link's URL fragment (login CSRF). */
import { it, expect, vi, beforeEach, afterEach } from 'vitest'

const ATTACKER_LINK = '/#access_token=attacker-jwt&refresh_token=attacker-rt&expires_in=3600&token_type=bearer'

beforeEach(() => {
  vi.resetModules()
  vi.stubEnv('VITE_SUPABASE_URL', 'http://localhost:54321')
  vi.stubEnv('VITE_SUPABASE_ANON_KEY', 'anon')
  localStorage.clear()
  globalThis.fetch = vi.fn(async () => new Response(
    JSON.stringify({ id: 'attacker', aud: 'authenticated', email: 'attacker@example.test' }),
    { status: 200, headers: { 'content-type': 'application/json' } }))
  window.history.replaceState(null, '', ATTACKER_LINK)
})

afterEach(() => {
  vi.unstubAllEnvs()
  window.history.replaceState(null, '', '/')
})

it('ignores an access token in the URL fragment', async () => {
  const { supabase } = await import('./supabase')

  const { data } = await supabase.auth.getSession()

  expect(data.session).toBeNull()
  // Adopting it starts with fetching the token's user; not even that may happen.
  expect(fetch.mock.calls.map(([url]) => String(url)).filter(u => u.includes('/auth/v1/user'))).toEqual([])
})
