import { useEffect, useState } from 'react'
import { Navigate } from 'react-router-dom'
import { useAuth } from '../../context/AuthContext'
import { apiFetch } from '../../lib/api'
import PageLoader from '../ui/PageLoader'

/**
 * Admin is decided by the backend, not by a role claim.
 *
 * Not `RoleGuard`: that reads a role the client can rewrite through Supabase,
 * which is fine for picking a dashboard but not for gating consent switches.
 * This is a UI convenience only -- every `/api/admin/*` endpoint checks again.
 */
export default function AdminGuard({ children }) {
  const { user, loading } = useAuth()
  // `null` means "haven't asked yet" and must not render as denied while the
  // check is in flight.
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
  // Redirect rather than error -- a non-admin here followed a stale link, not
  // a broken feature.
  if (!allowed) return <Navigate to="/dashboard" replace />

  return <>{children}</>
}
