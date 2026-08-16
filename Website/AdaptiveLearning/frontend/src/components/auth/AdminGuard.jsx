import { useEffect, useState } from 'react'
import { Navigate } from 'react-router-dom'
import { useAuth } from '../../context/AuthContext'
import { apiFetch } from '../../lib/api'
import PageLoader from '../ui/PageLoader'

/**
 * Admin is decided by the backend, not by a role claim.
 *
 * Deliberately not `RoleGuard`. That reads `role` from `AuthContext`, which
 * reads it from `user_metadata.role` -- set by the client at sign-up and
 * rewritable at any time through `supabase.auth.updateUser` without this
 * backend seeing it. That is fine for choosing which dashboard to show a
 * teacher; it is not fine for the switches that decide whether consent is
 * enforced. Membership lives in `admin_users`, which only the service-role
 * client can read, so the only way to know is to ask.
 *
 * This is a UI convenience, not the security boundary: every `/api/admin/*`
 * endpoint checks again on each request. Removing this component would make
 * the dashboard reachable and every button on it fail with a 403.
 */
export default function AdminGuard({ children }) {
  const { user, loading } = useAuth()
  // Three states, not two: `null` is "haven't asked yet", which must not
  // render as "denied" or the page redirects away while the check is in
  // flight.
  const [allowed, setAllowed] = useState(null)

  useEffect(() => {
    if (loading || !user) return
    let cancelled = false
    apiFetch('/api/admin/me')
      .then(() => { if (!cancelled) setAllowed(true) })
      .catch(() => { if (!cancelled) setAllowed(false) })
    return () => { cancelled = true }
  }, [loading, user])

  if (loading || (user && allowed === null)) return <PageLoader />
  if (!user) return <Navigate to="/login" replace />
  // A failed check sends them to their own dashboard rather than showing an
  // error: someone who is not an admin did not ask for this page, they
  // followed a stale link or typed the URL.
  if (!allowed) return <Navigate to="/dashboard" replace />

  return <>{children}</>
}
