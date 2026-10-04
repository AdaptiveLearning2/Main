/** The auth-only client keeps supabase-js's warning for an `sb_` key type it does not recognise. */
import { afterEach, describe, expect, it, vi } from 'vitest'

// Re-importing makes auth-js warn about several clients; only this warning is under test.
const KEY_WARNING = /Unrecognized Supabase API key format/
const URL_VAR = 'VITE_SUPABASE_URL'
const KEY_VAR = 'VITE_SUPABASE_ANON_KEY'

async function loadWith(value) {
  vi.resetModules()
  vi.stubEnv(URL_VAR, 'http://localhost:54321')
  vi.stubEnv(KEY_VAR, value)
  const warn = vi.spyOn(console, 'warn').mockImplementation(() => {})
  await import('./supabase')
  return warn
}

afterEach(() => {
  vi.unstubAllEnvs()
  vi.restoreAllMocks()
})

describe('the API key format check', () => {
  it('warns about an sb_ key type it does not know', async () => {
    const warn = await loadWith(['sb', 'odd', 'a'].join('_'))
    expect(warn).toHaveBeenCalledWith(expect.stringMatching(KEY_WARNING))
  })

  it.each([['publishable'], ['secret'], ['temp']])('stays quiet for an sb_%s key', async (kind) => {
    const warn = await loadWith(['sb', kind, 'a'].join('_'))
    expect(warn).not.toHaveBeenCalledWith(expect.stringMatching(KEY_WARNING))
  })

  it('stays quiet for a legacy key', async () => {
    const warn = await loadWith('anon')
    expect(warn).not.toHaveBeenCalledWith(expect.stringMatching(KEY_WARNING))
  })
})
