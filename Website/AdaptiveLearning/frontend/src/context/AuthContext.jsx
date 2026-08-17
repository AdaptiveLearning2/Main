import { createContext, useContext, useEffect, useRef, useState } from 'react'
import { supabase } from '../lib/supabase'
import { apiFetch } from '../lib/api'
import { clearViewPrefs } from '../lib/viewPrefs'

const AuthContext = createContext()

/** How long the app waits to find out what role someone has before falling back
 *  to the claim on their session.
 *
 *  This one read gates `loading`, which gates every route, so it is the one
 *  request in the app that must not be able to hang: a failure is caught below
 *  and resolves to the claimed role, but a request that never settles leaves
 *  `role` null and `loading` true for ever -- an infinite loader over the whole
 *  application for every signed-in user. The `.catch` is not a bound; this is.
 *
 *  Ten seconds because it is a single-row select on the hot path of every page
 *  load. Long enough that a slow connection still gets the authoritative answer,
 *  short enough that a stalled proxy costs a wait rather than a dead app.
 */
const ROLE_TIMEOUT_MS = 10000

export function AuthProvider({ children }) {
  const [user, setUser]       = useState(null)
  const [session, setSession] = useState(null)
  const [role, setRole]       = useState(null)
  const [authLoading, setAuthLoading] = useState(true)

  // What the *client* claims. Written at sign-up and rewritable at any time
  // with `supabase.auth.updateUser`, so it is a preference rather than a
  // permission -- used here only as the fallback for when the backend cannot be
  // reached, which is the one moment the authoritative answer is unavailable.
  const claimedRole = (u) => u?.user_metadata?.role || 'student'

  // Read by the effect below, which is keyed on the user *id* so it does not
  // re-run when the session object is replaced on a token refresh. The claimed
  // role still has to come from somewhere at that point, and a ref is how it
  // gets there without putting `user` in the dependency list.
  const userRef = useRef(null)
  userRef.current = user

  useEffect(() => {
    supabase.auth.getSession().then(({ data: { session } }) => {
      setSession(session)
      setUser(session?.user ?? null)
      setAuthLoading(false)
    })
    const { data: { subscription } } = supabase.auth.onAuthStateChange((event, session) => {
      // Covers the sign-outs that never go through signOut() below: an expired
      // or revoked refresh token, and a sign-out performed in another tab. The
      // preference is per-browser, not per-account, so every route out of a
      // session has to drop it.
      if (event === 'SIGNED_OUT') clearViewPrefs()
      setSession(session)
      setUser(session?.user ?? null)
      setAuthLoading(false)
      // Nothing here may await anything that reads the session. supabase-js
      // holds an internal auth lock while dispatching and `apiFetch` calls
      // `getSession()` for the token, so awaiting it here deadlocks -- and the
      // symptom is the app hanging on a loader for ever. The role read lives in
      // its own effect below for exactly that reason.
    })
    return () => subscription.unsubscribe()
  }, [])

  // The role comes from `profiles.role`, which the backend owns: the client
  // cannot write it (20260824010000) and sign-up cannot ask for `admin`
  // (20260824020000). Reading `user_metadata` instead is what made an
  // administrator's own console unreachable -- an account promoted in the SQL
  // editor has no `role` in its metadata at all, so it rendered as a student,
  // with the student nav, a badge reading "Student", and no link to /admin.
  //
  // Keyed on the id rather than the user object, so a token refresh does not
  // re-fetch. Runs outside the auth callback, per the lock note above.
  const userId = user?.id ?? null
  useEffect(() => {
    if (!userId) {
      setRole(null)
      return
    }
    let cancelled = false
    // Cleared first, so `loading` stays true while we find out. Without this a
    // second account signing in would be routed by the previous one's role for
    // as long as the request took.
    setRole(null)
    apiFetch('/api/profile/me', { timeoutMs: ROLE_TIMEOUT_MS })
      .then(p => { if (!cancelled) setRole(p?.role || claimedRole(userRef.current)) })
      // The backend being unreachable is not a demotion. Falling back to the
      // claim keeps a teacher on the teacher app, which is what happened before
      // this read existed; falling back to 'student' would drop every teacher
      // into the wrong application whenever the API blipped. This is the
      // opposite direction to `_role` on the backend, deliberately: that one
      // decides access, this one decides which nav to draw.
      .catch(() => { if (!cancelled) setRole(claimedRole(userRef.current)) })
    return () => { cancelled = true }
  }, [userId])

  // A signed-in user whose role has not resolved yet is still loading. Without
  // this the guards see `role === null` for a frame and render the "this
  // account isn't set up" screen at every page load.
  const loading = authLoading || (!!user && role === null)

  const signUp = async (email, password, selectedRole = 'student', displayName = '') => {
    const { error } = await supabase.auth.signUp({
      email,
      password,
      options: { data: { role: selectedRole, display_name: displayName || email.split('@')[0] } },
    })
    if (error) throw error
  }

  const signIn = async (email, password) => {
    const { error } = await supabase.auth.signInWithPassword({ email, password })
    if (error) throw error
  }

  const signOut = async () => {
    try {
      await supabase.auth.signOut()
    } finally {
      // In a finally, not after the await: a sign-out that fails on the network
      // still leaves the user at the login screen, and the preference lives in
      // localStorage, which is scoped to the browser rather than the account.
      // Leaving it behind on a shared machine hands the next person to sign in
      // a privacy setting they never chose.
      clearViewPrefs()
    }
  }

  return (
    <AuthContext.Provider value={{ user, session, role, loading, signUp, signIn, signOut }}>
      {children}
    </AuthContext.Provider>
  )
}

export function useAuth() {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth must be inside AuthProvider')
  return ctx
}