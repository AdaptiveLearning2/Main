import { Navigate } from 'react-router-dom'
import { useAuth } from '../../context/AuthContext'
import PageLoader from '../ui/PageLoader'

export default function RoleGuard({ roles, children }) {
  const { user, role, loading } = useAuth()

  if (loading) return <PageLoader />
  if (!user)   return <Navigate to="/login" replace />
  if (roles && !roles.includes(role)) {
    return <Navigate to={role === 'teacher' ? '/teacher' : '/dashboard'} replace />
  }

  return <>{children}</>
}