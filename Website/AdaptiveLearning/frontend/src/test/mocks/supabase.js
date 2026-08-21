import { vi } from 'vitest'

/**
 * The shared `lib/supabase` mock.
 *
 *     vi.mock('../../lib/supabase', async () => await import('../../test/mocks/supabase'))
 *     import { setSession, fireAuthEvent, authFns, resetSupabaseMock,
 *              buildAuthSession } from '../../test/mocks/supabase'
 *
 * Mocking the module (not the client it builds) also avoids
 * `lib/supabase.js` throwing at import when `VITE_SUPABASE_URL` /
 * `VITE_SUPABASE_ANON_KEY` are unset, which is the case in CI's test step.
 */

/** Every auth method as its own spy, so a test can assert on calls and
 *  steer responses. Exposed separately since `supabase.auth` below just
 *  delegates to these and isn't itself a spy. */
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

/** A session with a role claim on it.
 *
 *  `user_metadata.role` is what `AuthContext` falls back to, but is
 *  client-writable and not trusted by the backend — set it here to test
 *  that a guard isn't fooled by it.
 */
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
      // Absent, not null, when no role is claimed — matches a real account
      // promoted directly in the database, which has no `role` key at all.
      user_metadata: role === null ? {} : { role },
    },
    ...rest,
  }
}

/** What `getSession()` resolves with from here on. `null` for signed out. */
export function setSession(session) {
  authFns.getSession.mockResolvedValue({ data: { session } })
}

/** Deliver an auth event to every live `onAuthStateChange` subscriber.
 *
 *  Needed to reach cases like `SIGNED_OUT` from an expired refresh token
 *  (no one calls `signOut()`), and `TOKEN_REFRESHED` handling, which must
 *  not await `getSession()` inside the callback since supabase-js holds an
 *  auth lock during dispatch and that would deadlock.
 */
export function fireAuthEvent(event, session = null) {
  for (const cb of [...subscribers]) cb(event, session)
}

/** True while something is still subscribed — asserts a provider
 *  unsubscribes on unmount instead of leaking a stale callback. */
export function subscriberCount() {
  return subscribers.length
}

export function resetSupabaseMock() {
  subscribers = []
  for (const fn of Object.values(authFns)) fn.mockReset()
  // Signed out by default, so a test that forgets to set a session gets a
  // real empty case instead of an undefined destructure.
  setSession(null)
  authFns.signOut.mockResolvedValue({ error: null })
  authFns.signUp.mockResolvedValue({ data: {}, error: null })
  authFns.signInWithPassword.mockResolvedValue({ data: {}, error: null })
  authFns.getUser.mockResolvedValue({ data: { user: null }, error: null })
}

resetSupabaseMock()
