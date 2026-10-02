import { useEffect, useState } from 'react'
import { Navigate } from 'react-router-dom'
import { useAuth } from '../../context/AuthContext'
import { apiFetch } from '../../lib/api'
import LoadError from '../ui/LoadError'
import PageLoader from '../ui/PageLoader'

/**
 * Admin is decided by the backend (`/api/admin/me`), not by a role claim.
 * A UI convenience only: every `/api/admin/*` endpoint checks again.
 */
export default function AdminGuard({ children }) {
  const { user, loading } = useAuth()
  // `null`: not asked yet, never rendered as denied.
  const [allowed, setAllowed] = useState(null)
  // A check that failed rather than refused: only a 403 says "not an admin".
  const [failed, setFailed] = useState(null)
  const [attempt, setAttempt] = useState(0)

  useEffect(() => {
    if (loading || !user) return
    let cancelled = false
    apiFetch('/api/admin/me')
      .then(() => { if (!cancelled) setAllowed(true) })
      .catch((e) => {
        if (cancelled) return
        if (e?.status === 403) setAllowed(false)
        else setFailed(e)
      })
    return () => { cancelled = true }
  }, [loading, user, attempt])

  function retry() {
    setFailed(null)
    setAttempt(n => n + 1)
  }

  if (loading || (user && allowed === null && !failed)) return <PageLoader />
  if (!user) return <Navigate to="/login" replace />
  if (failed) return <LoadError what="the admin console" error={failed} onRetry={retry} />
  if (!allowed) return <Navigate to="/dashboard" replace />

  return <>{children}</>
}
