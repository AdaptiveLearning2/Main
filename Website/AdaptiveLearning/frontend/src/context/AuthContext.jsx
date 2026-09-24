import { createContext, useCallback, useContext, useEffect, useRef, useState } from 'react'
import { supabase } from '../lib/supabase'
import { apiFetch } from '../lib/api'
import { clearViewPrefs } from '../lib/viewPrefs'

const AuthContext = createContext()

/** Role read timeout: it gates `loading` for every route, and a `.catch` is not a bound. */
const ROLE_TIMEOUT_MS = 10000

export function AuthProvider({ children }) {
  const [user, setUser]       = useState(null)
  const [session, setSession] = useState(null)
  const [role, setRole]       = useState(null)
  // `profiles.display_name`, from the same read as the role.
  const [profileName, setProfileName] = useState(null)
  const [authLoading, setAuthLoading] = useState(true)

  // Client-writable, not authoritative: fallback only when the backend is unreachable.
  const claimedRole = (u) => u?.user_metadata?.role || 'student'

  // Fallback name chain, worst case last.
  const claimedName = (u) =>
    u?.user_metadata?.display_name?.trim() || u?.email?.split('@')[0] || null

  // Lets the id-keyed role effect read the current user without re-running.
  const userRef = useRef(null)
  userRef.current = user

  useEffect(() => {
    supabase.auth.getSession().then(({ data: { session } }) => {
      setSession(session)
      setUser(session?.user ?? null)
      setAuthLoading(false)
    })
    const { data: { subscription } } = supabase.auth.onAuthStateChange((event, session) => {
      // Also sign-outs not via signOut(): expired refresh token, another tab.
      if (event === 'SIGNED_OUT') clearViewPrefs()
      setSession(session)
      setUser(session?.user ?? null)
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

  useEffect(() => {
    if (!userId) {
      setRole(null)
      setProfileName(null)
      return
    }
    let cancelled = false
    // Cleared first so a new account is never routed or greeted as the previous one.
    setRole(null)
    setProfileName(null)
    loadProfile()
      .then(p => {
        if (cancelled) return
        setRole(p?.role || claimedRole(userRef.current))
        setProfileName(p?.display_name?.trim() || null)
      })
      // A blip is not a demotion: fall back to the claim, not 'student'.
      .catch(() => { if (!cancelled) setRole(claimedRole(userRef.current)) })
    return () => { cancelled = true }
  }, [userId, loadProfile])

  // Re-read after a profile save. Swallows failure: the save already succeeded.
  const refreshProfile = useCallback(async () => {
    try {
      const p = await loadProfile()
      setProfileName(p?.display_name?.trim() || null)
    } catch { /* keep the name we have */ }
  }, [loadProfile])

  const displayName = profileName || claimedName(user)

  // No role yet means still loading, or guards flash "account isn't set up".
  const loading = authLoading || (!!user && role === null)

  // `chosenName`, not `displayName`, to avoid shadowing the current user's name.
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
      // Even on a failed sign-out, for shared machines.
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