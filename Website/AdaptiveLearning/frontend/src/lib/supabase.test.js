/** Auth client settings are explicit; expected values are literals, not imports. */
import { it, expect, vi, beforeEach } from 'vitest'

const construct = vi.fn()
vi.mock('@supabase/auth-js', () => ({
  AuthClient: class { constructor(options) { construct(options) } },
}))

beforeEach(() => {
  construct.mockClear()
  vi.resetModules()
  vi.stubEnv('VITE_SUPABASE_URL', 'http://localhost:54321')
  vi.stubEnv('VITE_SUPABASE_ANON_KEY', 'anon')
})

it('creates the auth client with the settings written out', async () => {
  await import('./supabase')

  expect(construct).toHaveBeenCalledTimes(1)
  expect(construct.mock.calls[0][0]).toEqual({
    url: 'http://localhost:54321/auth/v1',
    headers: { Authorization: 'Bearer anon', apikey: 'anon' },
    storageKey: 'sb-localhost-auth-token',
    autoRefreshToken: true,
    persistSession: true,
    detectSessionInUrl: false,
    flowType: 'implicit',
  })
})

it.each([
  ['a hosted project', 'https://abcdefghijklmnop.supabase.co'],
  ['a hosted project with a trailing slash', 'https://abcdefghijklmnop.supabase.co/'],
])('keeps supabase-js\'s storage key for %s, so nobody is signed out', async (_name, url) => {
  // The key supabase-js derives: `sb-` + the first label of the host + `-auth-token`.
  vi.stubEnv('VITE_SUPABASE_URL', url)
  await import('./supabase')

  const options = construct.mock.calls[0][0]
  expect(options.storageKey).toBe('sb-abcdefghijklmnop-auth-token')
  expect(options.url).toBe('https://abcdefghijklmnop.supabase.co/auth/v1')
})
