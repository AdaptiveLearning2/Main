import { createContext, useContext, useEffect, useState } from 'react'
import { supabase } from '../lib/supabase'
import { clearFacePref } from '../lib/facePref'

const AuthContext = createContext()

export function AuthProvider({ children }) {
  const [user, setUser]       = useState(null)
  const [session, setSession] = useState(null)
  const [role, setRole]       = useState(null)
  const [loading, setLoading] = useState(true)

  const extractRole = (u) => u?.user_metadata?.role || 'student'

  useEffect(() => {
    supabase.auth.getSession().then(({ data: { session } }) => {
      setSession(session)
      setUser(session?.user ?? null)
      setRole(session?.user ? extractRole(session.user) : null)
      setLoading(false)
    })
    const { data: { subscription } } = supabase.auth.onAuthStateChange((event, session) => {
      // Covers the sign-outs that never go through signOut() below: an expired
      // or revoked refresh token, and a sign-out performed in another tab. The
      // preference is per-browser, not per-account, so every route out of a
      // session has to drop it.
      if (event === 'SIGNED_OUT') clearFacePref()
      setSession(session)
      setUser(session?.user ?? null)
      setRole(session?.user ? extractRole(session.user) : null)
      setLoading(false)
    })
    return () => subscription.unsubscribe()
  }, [])

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
      clearFacePref()
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