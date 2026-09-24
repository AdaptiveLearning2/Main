import { Navigate } from 'react-router-dom'
import { useAuth } from '../../context/AuthContext'
import PageLoader from '../ui/PageLoader'
import { homeFor } from '../../lib/homeRoute'

/** `/`: send each role to its own app, after auth has loaded. */
export default function HomeRedirect() {
  const { user, role, loading } = useAuth()

  if (loading) return <PageLoader />
  if (!user)   return <Navigate to="/login" replace />

  // Unrecognised role: the guarded student route explains itself.
  return <Navigate to={homeFor(role) || '/dashboard'} replace />
}
