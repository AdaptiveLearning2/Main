import { Navigate } from 'react-router-dom'
import { useAuth } from '../../context/AuthContext'
import PageLoader from '../ui/PageLoader'
import { homeFor } from '../../lib/homeRoute'

/** `/` -- send each role to its own app.
 *
 * Waits for auth rather than guessing: redirecting while still loading reads
 * `role` as undefined and sends a signed-in parent to the login page, which
 * looks like being logged out at random.
 */
export default function HomeRedirect() {
  const { user, role, loading } = useAuth()

  if (loading) return <PageLoader />
  if (!user)   return <Navigate to="/login" replace />

  // No home for an unrecognised role, so fall through to the guarded student
  // route, which explains itself rather than bouncing.
  return <Navigate to={homeFor(role) || '/dashboard'} replace />
}
