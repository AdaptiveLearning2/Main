import { AuthClient } from '@supabase/auth-js'

const supabaseUrl = import.meta.env.VITE_SUPABASE_URL
const supabaseAnonKey = import.meta.env.VITE_SUPABASE_ANON_KEY

if (!supabaseUrl || !supabaseAnonKey) {
  throw new Error('Missing Supabase environment variables')
}

// Auth security settings, explicit. persistSession uses localStorage: acceptable only while no XSS sink exists.
// detectSessionInUrl off: on, any link with an #access_token fragment signs the reader in as its sender.
// Email redirect links need it back on, with flowType 'pkce' at the same time.
const SUPABASE_AUTH_OPTIONS = {
  autoRefreshToken: true,
  persistSession: true,
  detectSessionInUrl: false,
  flowType: 'implicit',
}

const base = new URL(supabaseUrl.trim().replace(/\/*$/, '/'))

// Auth only: every table read goes through the backend. Built as supabase-js builds its own,
// storage key included; a different key would sign every user out.
export const supabase = {
  auth: new AuthClient({
    url: new URL('auth/v1', base).href,
    headers: { Authorization: `Bearer ${supabaseAnonKey}`, apikey: supabaseAnonKey },
    storageKey: `sb-${base.hostname.split('.')[0]}-auth-token`,
    ...SUPABASE_AUTH_OPTIONS,
  }),
}
