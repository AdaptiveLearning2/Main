import { createClient } from '@supabase/supabase-js'

const supabaseUrl = import.meta.env.VITE_SUPABASE_URL
const supabaseAnonKey = import.meta.env.VITE_SUPABASE_ANON_KEY

if (!supabaseUrl || !supabaseAnonKey) {
  throw new Error('Missing Supabase environment variables')
}

// The session's security settings, written out rather than left to the SDK's
// defaults so that an edit changing one -- a custom storage adapter, say -- has
// to say so here. These *are* the defaults today:
//  - autoRefreshToken: the access token is short-lived (`jwt_expiry`) and
//    replaced from the refresh token, which bounds the life of a stolen one.
//  - persistSession: kept in localStorage. Acceptable only while no page can
//    run injected script -- see "The XSS sinks" in CLAUDE.md.
//  - detectSessionInUrl: reads a session out of an auth redirect, which email
//    confirmation and password-reset links arrive as.
//  - flowType: how that redirect carries the session. 'implicit' puts the
//    tokens in the URL fragment; 'pkce' sends a one-time code that only the
//    browser which started the flow can exchange, which is stronger and also
//    means a link opened on another device (a confirmation email read on a
//    phone) fails. No redirect flow is in use yet, so this stays on today's
//    default -- choose deliberately when email links are switched on.
const SUPABASE_AUTH_OPTIONS = {
  autoRefreshToken: true,
  persistSession: true,
  detectSessionInUrl: true,
  flowType: 'implicit',
}

export const supabase = createClient(supabaseUrl, supabaseAnonKey, {
  auth: SUPABASE_AUTH_OPTIONS,
})
