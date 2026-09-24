import { useEffect, useState } from 'react'
import { Navigate } from 'react-router-dom'
import { useAuth } from '../../context/AuthContext'
import { apiFetch } from '../../lib/api'
import PageLoader from '../ui/PageLoader'

/**
 * Admin is decided by the backend (`/api/admin/me`), not by a role claim.
 * A UI convenience only: every `/api/admin/*` endpoint checks again.
 */
export default function AdminGuard({ children }) {
  const { user, loading } = useAuth()
  // `null`: not asked yet, never rendered as denied.
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
  if (!allowed) return <Navigate to="/dashboard" replace />

  return <>{children}</>
}
