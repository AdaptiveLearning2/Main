import { createContext, useCallback, useContext, useEffect, useRef, useState } from 'react'
import { supabase } from '../lib/supabase'
import { apiFetch } from '../lib/api'
import { clearViewPrefs } from '../lib/viewPrefs'

const AuthContext = createContext()

/** How long the app waits to find out a user's role before falling back to
 *  the claim on their session.
 *
 *  This read gates `loading`, which gates every route, so it must not be
 *  able to hang forever — a `.catch` alone isn't enough, since a request
 *  that never settles leaves `loading` true forever regardless.
 */
const ROLE_TIMEOUT_MS = 10000

export function AuthProvider({ children }) {
  const [user, setUser]       = useState(null)
  const [session, setSession] = useState(null)
  const [role, setRole]       = useState(null)
  // `profiles.display_name`, from the same read as the role. Every greeting
  // and sidebar used to derive a name from the email local part instead, so a
  // student who set their name in Profile saw it on that one page and their
  // email prefix everywhere else — and the two only agreed until the first
  // edit, since sign-up seeds the stored name *from* the email prefix. The
  // right value was already arriving in this payload and being discarded.
  const [profileName, setProfileName] = useState(null)
  const [authLoading, setAuthLoading] = useState(true)

  // What the client claims, not authoritative — user_metadata can be
  // rewritten by the client itself. Used only as a fallback when the
  // backend can't be reached.
  const claimedRole = (u) => u?.user_metadata?.role || 'student'

  // The name fallback chain, worst case last. The claim is preferred over the
  // email because the backend mirrors the stored name into `user_metadata` on
  // every save — best-effort, so it can be stale, but stale-and-a-name beats
  // an email prefix that was never a name. Null only for a user with neither.
  const claimedName = (u) =>
    u?.user_metadata?.display_name?.trim() || u?.email?.split('@')[0] || null

  // A ref so the role-fetch effect (keyed on user id, not the user object)
  // can still read the current user without re-running on token refresh.
  const userRef = useRef(null)
  userRef.current = user

  useEffect(() => {
    supabase.auth.getSession().then(({ data: { session } }) => {
      setSession(session)
      setUser(session?.user ?? null)
      setAuthLoading(false)
    })
    const { data: { subscription } } = supabase.auth.onAuthStateChange((event, session) => {
      // Covers sign-outs that don't go through signOut() below: an expired
      // refresh token, or a sign-out in another tab.
      if (event === 'SIGNED_OUT') clearViewPrefs()
      setSession(session)
      setUser(session?.user ?? null)
      setAuthLoading(false)
      // Nothing here may await anything that reads the session — supabase-js
      // holds an auth lock during this callback, so calling `getSession()`
      // (which `apiFetch` does) here would deadlock. That's why the role
      // read below runs in its own effect instead.
    })
    return () => subscription.unsubscribe()
  }, [])

  // The role comes from `profiles.role`, which the backend owns and the
  // client can't write — reading `user_metadata` instead would miss an
  // account promoted to admin directly in the database.
  //
  // Keyed on id, not the user object, so a token refresh doesn't re-fetch.
  // Runs outside the auth callback, per the lock note above.
  const userId = user?.id ?? null
  // Extracted so Profile can re-read after a save. Without that the greeting
  // keeps the name from sign-in until the next reload — the same staleness
  // this fixes, one value along.
  const loadProfile = useCallback(() => {
    if (!userId) return Promise.resolve(null)
    return apiFetch('/api/profile/me', { timeoutMs: ROLE_TIMEOUT_MS })
  }, [userId])

  useEffect(() => {
    if (!userId) {
      setRole(null)
      setProfileName(null)
      return
    }
    let cancelled = false
    // Cleared first so `loading` stays true — otherwise a second account
    // signing in would briefly be routed by the previous one's role. The name
    // is cleared with it, or the new account is greeted by the old one's.
    setRole(null)
    setProfileName(null)
    loadProfile()
      .then(p => {
        if (cancelled) return
        setRole(p?.role || claimedRole(userRef.current))
        setProfileName(p?.display_name?.trim() || null)
      })
      // Backend unreachable is not a demotion — fall back to the claimed
      // role so a teacher stays in the teacher app instead of dropping to
      // student on an API blip. The name falls back the same way, below.
      .catch(() => { if (!cancelled) setRole(claimedRole(userRef.current)) })
    return () => { cancelled = true }
  }, [userId, loadProfile])

  // Re-read after a profile save, so a renamed student is greeted by the name
  // they just chose. Swallows its own failure: the save already succeeded and
  // told them so, and the stale greeting is fixed by the next page load.
  const refreshProfile = useCallback(async () => {
    try {
      const p = await loadProfile()
      setProfileName(p?.display_name?.trim() || null)
    } catch { /* keep the name we have */ }
  }, [loadProfile])

  // Never null for a signed-in account with an email, so no caller needs to
  // know about the fallbacks — only whether it has a user at all.
  const displayName = profileName || claimedName(user)

  // A signed-in user with no role yet is still loading, or the guards would
  // briefly show "this account isn't set up" on every page load.
  const loading = authLoading || (!!user && role === null)

  // The parameter is `chosenName`, not `displayName`: the name above is the
  // *current* user's, and a shadow here would read as this function setting it.
  const signUp = async (email, password, selectedRole = 'student', chosenName = '') => {
    const { error } = await supabase.auth.signUp({
      email,
      password,
      options: { data: { role: selectedRole, display_name: chosenName || email.split('@')[0] } },
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
      // In `finally` so this runs even if sign-out fails on the network —
      // otherwise the next person on a shared machine inherits this
      // browser's stored preference.
      clearViewPrefs()
    }
  }

  return (
    <AuthContext.Provider value={{ user, session, role, displayName, loading, refreshProfile, signUp, signIn, signOut }}>
      {children}
    </AuthContext.Provider>
  )
}

export function useAuth() {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth must be inside AuthProvider')
  return ctx
}