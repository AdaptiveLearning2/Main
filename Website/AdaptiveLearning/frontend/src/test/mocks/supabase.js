import { vi } from 'vitest'

/**
 * The shared `lib/supabase` mock; mocking the module also avoids its import-time throw on unset `VITE_SUPABASE_*`.
 *     vi.mock('../../lib/supabase', async () => await import('../../test/mocks/supabase'))
 *     import { setSession, fireAuthEvent, authFns, resetSupabaseMock, buildAuthSession } from '../../test/mocks/supabase'
 */

/** One spy per auth method; `supabase.auth` below delegates to these. */
export const authFns = {
  getSession: vi.fn(),
  signUp: vi.fn(),
  signInWithPassword: vi.fn(),
  signOut: vi.fn(),
  getUser: vi.fn(),
  updateUser: vi.fn(),
}

let subscribers = []

export const supabase = {
  auth: {
    getSession: (...a) => authFns.getSession(...a),
    signUp: (...a) => authFns.signUp(...a),
    signInWithPassword: (...a) => authFns.signInWithPassword(...a),
    signOut: (...a) => authFns.signOut(...a),
    getUser: (...a) => authFns.getUser(...a),
    updateUser: (...a) => authFns.updateUser(...a),
    onAuthStateChange: (cb) => {
      subscribers.push(cb)
      return {
        data: {
          subscription: {
            unsubscribe: () => { subscribers = subscribers.filter(s => s !== cb) },
          },
        },
      }
    },
  },
}

/** A session with a (client-writable, untrusted) `user_metadata.role` claim. */
export function buildAuthSession({
  role = 'student',
  id = 'user-1',
  email = 'ada@example.com',
  accessToken = 'access-token-1',
  ...rest
} = {}) {
  return {
    access_token: accessToken,
    user: {
      id,
      email,
      // `role: null` means no key at all, like an account promoted in the database.
      user_metadata: role === null ? {} : { role },
    },
    ...rest,
  }
}

/** What `getSession()` resolves with from here on. `null` for signed out. */
export function setSession(session) {
  authFns.getSession.mockResolvedValue({ data: { session } })
}

/** Deliver an auth event (e.g. `SIGNED_OUT`, `TOKEN_REFRESHED`) to every live subscriber. */
export function fireAuthEvent(event, session = null) {
  for (const cb of [...subscribers]) cb(event, session)
}

/** Live subscriber count, for asserting unsubscribe on unmount. */
export function subscriberCount() {
  return subscribers.length
}

export function resetSupabaseMock() {
  subscribers = []
  for (const fn of Object.values(authFns)) fn.mockReset()
  // Signed out by default.
  setSession(null)
  authFns.signOut.mockResolvedValue({ error: null })
  authFns.signUp.mockResolvedValue({ data: {}, error: null })
  authFns.signInWithPassword.mockResolvedValue({ data: {}, error: null })
  authFns.getUser.mockResolvedValue({ data: { user: null }, error: null })
}

resetSupabaseMock()
