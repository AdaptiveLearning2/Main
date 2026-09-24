import { createClient } from '@supabase/supabase-js'

const supabaseUrl = import.meta.env.VITE_SUPABASE_URL
const supabaseAnonKey = import.meta.env.VITE_SUPABASE_ANON_KEY

if (!supabaseUrl || !supabaseAnonKey) {
  throw new Error('Missing Supabase environment variables')
}

// Auth security settings, explicit (currently the SDK defaults).
// persistSession uses localStorage: acceptable only while no XSS sink exists.
// flowType: revisit 'pkce' when email redirect links are switched on.
const SUPABASE_AUTH_OPTIONS = {
  autoRefreshToken: true,
  persistSession: true,
  detectSessionInUrl: true,
  flowType: 'implicit',
}

export const supabase = createClient(supabaseUrl, supabaseAnonKey, {
  auth: SUPABASE_AUTH_OPTIONS,
})
