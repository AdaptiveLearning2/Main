/**
 * The session's security settings are passed explicitly, so a change to one is
 * a change to this file rather than to a dependency's default. The expected
 * values are written out here, not imported, or the test would compare the
 * setting with itself.
 */
import { it, expect, vi, beforeEach } from 'vitest'

const createClient = vi.fn(() => ({}))
vi.mock('@supabase/supabase-js', () => ({ createClient: (...a) => createClient(...a) }))

beforeEach(() => {
  createClient.mockClear()
  vi.resetModules()
  vi.stubEnv('VITE_SUPABASE_URL', 'http://localhost:54321')
  vi.stubEnv('VITE_SUPABASE_ANON_KEY', 'anon')
})

it('creates the client with the auth settings written out', async () => {
  await import('./supabase')

  expect(createClient).toHaveBeenCalledTimes(1)
  const [url, key, options] = createClient.mock.calls[0]
  expect([url, key]).toEqual(['http://localhost:54321', 'anon'])
  expect(options?.auth).toEqual({
    autoRefreshToken: true,
    persistSession: true,
    detectSessionInUrl: true,
  })
})
