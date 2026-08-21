import { Navigate } from 'react-router-dom'
import { useAuth } from '../../context/AuthContext'
import PageLoader from '../ui/PageLoader'
import { homeFor } from '../../lib/homeRoute'

export default function RoleGuard({ roles, children }) {
  const { user, role, loading } = useAuth()

  if (loading) return <PageLoader />
  if (!user)   return <Navigate to="/login" replace />

  if (roles && !roles.includes(role)) {
    const home = homeFor(role)
    // An unrecognised role has no home to send them to. Guessing one would
    // loop, since every candidate route is guarded and would land back here.
    if (!home) {
      return (
        <div className="min-h-screen grid place-items-center p-8 text-center">
          <div>
            <p className="font-black text-lg text-gray-900 dark:text-white mb-1">
              This account isn&apos;t set up yet
            </p>
            <p className="text-sm text-gray-500 dark:text-gray-400">
              It has no role assigned, so there is nothing to show. Ask your
              teacher or administrator to finish setting it up.
            </p>
          </div>
        </div>
      )
    }
    return <Navigate to={home} replace />
  }

  return <>{children}</>
}
