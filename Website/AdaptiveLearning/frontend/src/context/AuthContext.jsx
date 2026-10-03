import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react'
import { supabase } from '../lib/supabase'
import { apiFetch } from '../lib/api'
import { clearViewPrefs } from '../lib/viewPrefs'
import { runSignOutTasks } from '../lib/signOutTasks'

const AuthContext = createContext()

/** Role read timeout: it gates `loading` for every route, and a `.catch` is not a bound. */
const ROLE_TIMEOUT_MS = 10000
// The retry after a timeout: Render's free tier can take most of a minute to wake.
const COLD_START_ROLE_TIMEOUT_MS = 50_000

const NO_PROFILE = { id: null, role: null, name: null }

export function AuthProvider({ children }) {
  const [user, setUser]       = useState(null)
  const [session, setSession] = useState(null)
  // `profiles.role` and `display_name` from one read, with the user id they were read for.
  const [profile, setProfile] = useState(NO_PROFILE)
  const [authLoading, setAuthLoading] = useState(true)

  // Client-writable, not authoritative: fallback only when the backend is unreachable.
  const claimedRole = (u) => u?.user_metadata?.role || 'student'

  // Fallback name chain, worst case last.
  const claimedName = (u) =>
    u?.user_metadata?.display_name?.trim() || u?.email?.split('@')[0] || null

  // Lets the id-keyed role effect read the current user without re-running.
  const userRef = useRef(null)
  useEffect(() => { userRef.current = user }, [user])

  useEffect(() => {
    supabase.auth.getSession().then(({ data: { session } }) => {
      setSession(session)
      setUser(session?.user ?? null)
      setAuthLoading(false)
    })
    const { data: { subscription } } = supabase.auth.onAuthStateChange((event, session) => {
      // Also sign-outs not via signOut(): expired refresh token, another tab.
      if (event === 'SIGNED_OUT') clearViewPrefs()
      // Signing back in as the same account reads its role again rather than reusing this one.
      if (!session?.user) setProfile(NO_PROFILE)
      setSession(session)
      // Same account, unchanged record (a token refresh): keep the object, so nothing keyed on it re-runs.
      const next = session?.user ?? null
      setUser(prev => (prev && next && event !== 'USER_UPDATED' && prev.id === next.id
                       && prev.updated_at === next.updated_at ? prev : next))
      setAuthLoading(false)
      // Never await a session read here (e.g. `apiFetch`): supabase-js holds an auth lock, so it deadlocks.
    })
    return () => subscription.unsubscribe()
  }, [])

  // Role from `profiles.role` (backend-owned). Keyed on id so a token refresh doesn't re-fetch.
  const userId = user?.id ?? null
  const loadProfile = useCallback(() => {
    if (!userId) return Promise.resolve(null)
    return apiFetch('/api/profile/me', { timeoutMs: ROLE_TIMEOUT_MS })
  }, [userId])

  // Derived, never reset: a new account is never routed or greeted as the previous one.
  const role = profile.id === userId ? profile.role : null
  const profileName = profile.id === userId ? profile.name : null

  useEffect(() => {
    if (!userId) return
    let cancelled = false
    loadProfile()
      // A sleeping server's boot can outlast the first bound: one longer, still bounded, try.
      .catch(e => (e?.timeout && !cancelled
        ? apiFetch('/api/profile/me', { timeoutMs: COLD_START_ROLE_TIMEOUT_MS })
        : Promise.reject(e)))
      .then(p => {
        if (cancelled) return
        setProfile({ id: userId, role: p?.role || claimedRole(userRef.current),
                     name: p?.display_name?.trim() || null })
      })
      // A blip is not a demotion: fall back to the claim, not 'student'.
      .catch(() => {
        if (!cancelled) setProfile({ id: userId, role: claimedRole(userRef.current), name: null })
      })
    return () => { cancelled = true }
  }, [userId, loadProfile])

  // Re-read after a profile save. Swallows failure: the save already succeeded.
  const refreshProfile = useCallback(() => loadProfile()
    .then(p => setProfile(prev => (prev.id === userId
      ? { ...prev, name: p?.display_name?.trim() || null } : prev)))
    .catch(() => { /* keep the name we have */ }), [loadProfile, userId])

  const displayName = profileName || claimedName(user)

  // No role yet means still loading, or guards flash "account isn't set up".
  const loading = authLoading || (!!user && role === null)

  // `chosenName`, not `displayName`, to avoid shadowing the current user's name.
  const signUp = useCallback(async (email, password, selectedRole = 'student', chosenName = '', grade = '') => {
    const data = { role: selectedRole, display_name: chosenName || email.split('@')[0] }
    // Only a student has a grade of their own; `handle_new_user` keeps only a dropdown label.
    if (selectedRole === 'student' && grade) data.grade_level = grade
    const { error } = await supabase.auth.signUp({ email, password, options: { data } })
    if (error) throw error
  }, [])

  const signIn = useCallback(async (email, password) => {
    const { error } = await supabase.auth.signInWithPassword({ email, password })
    if (error) throw error
  }, [])

  // One sign-out at a time: a repeat click joins the one in progress.
  const signingOut = useRef(null)
  const signOut = useCallback(() => {
    if (!signingOut.current) {
      signingOut.current = (async () => {
        // Before the token is cleared; see `lib/signOutTasks.js`.
        await runSignOutTasks()
        // Even on a failed sign-out, for shared machines.
        await Promise.resolve().then(() => supabase.auth.signOut()).finally(clearViewPrefs)
      })().finally(() => { signingOut.current = null })
    }
    return signingOut.current
  }, [])

  // Every consumer re-renders when this object changes, so it changes only with its contents.
  const value = useMemo(
    () => ({ user, session, role, displayName, loading, refreshProfile, signUp, signIn, signOut }),
    [user, session, role, displayName, loading, refreshProfile, signUp, signIn, signOut])

  return (
    <AuthContext.Provider value={value}>
      {children}
    </AuthContext.Provider>
  )
}

export function useAuth() {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth must be inside AuthProvider')
  return ctx
}