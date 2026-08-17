import { vi } from 'vitest'

/**
 * The shared `lib/supabase` mock.
 *
 *     vi.mock('../../lib/supabase', async () => await import('../../test/mocks/supabase'))
 *     import { setSession, fireAuthEvent, authFns, resetSupabaseMock,
 *              buildAuthSession } from '../../test/mocks/supabase'
 *
 * Mocking the module (rather than the client it builds) also sidesteps
 * `lib/supabase.js` throwing at import when `VITE_SUPABASE_URL` /
 * `VITE_SUPABASE_ANON_KEY` are unset -- which they are in CI, where the build
 * step supplies them and the test step does not.
 */

/** Every auth method as its own spy, so a test can assert on the call as well
 *  as steer the answer. Exposed rather than reached through `supabase.auth`,
 *  because the object below delegates lazily and is not itself a spy. */
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
 *  `user_metadata.role` is what `AuthContext` falls back to and what the backend
 *  refuses to trust -- it is client-writable, so a test that wants to prove a
 *  guard is not fooled by it sets it here and expects to be ignored.
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
      // Absent rather than null when no role is claimed: an account promoted in
      // the SQL editor has no `role` key at all, which is the shape that made
      // an administrator render as a student.
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
 *  Several properties are only reachable this way: the `SIGNED_OUT` cleanup
 *  that an expired refresh token triggers without anyone calling `signOut()`,
 *  and the `TOKEN_REFRESHED` handling that must not await anything reading the
 *  session (supabase-js holds an auth lock while dispatching, so awaiting
 *  `getSession()` inside the callback deadlocks).
 */
export function fireAuthEvent(event, session = null) {
  for (const cb of [...subscribers]) cb(event, session)
}

/** True while something is still subscribed -- for asserting a provider
 *  unsubscribes on unmount rather than leaving a callback holding stale state. */
export function subscriberCount() {
  return subscribers.length
}

export function resetSupabaseMock() {
  subscribers = []
  for (const fn of Object.values(authFns)) fn.mockReset()
  // Signed out unless a test says otherwise, so a file that forgets to set a
  // session gets the honest empty case rather than an undefined destructure.
  setSession(null)
  authFns.signOut.mockResolvedValue({ error: null })
  authFns.signUp.mockResolvedValue({ data: {}, error: null })
  authFns.signInWithPassword.mockResolvedValue({ data: {}, error: null })
  authFns.getUser.mockResolvedValue({ data: { user: null }, error: null })
}

resetSupabaseMock()
