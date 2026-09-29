import { createClient } from '@supabase/supabase-js'

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

export const supabase = createClient(supabaseUrl, supabaseAnonKey, {
  auth: SUPABASE_AUTH_OPTIONS,
})
