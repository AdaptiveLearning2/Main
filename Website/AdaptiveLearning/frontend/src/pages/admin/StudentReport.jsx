import { useParams, useLocation } from 'react-router-dom'
import StudentProgressReport from '../../components/reports/StudentProgressReport'

/**
 * A student's report for an admin, from the console's search. Its own route: the teacher one
 * sits behind RoleGuard's teacher role, and the backend admits an admin by relationship.
 */
export default function AdminStudentReport() {
  const { id } = useParams()
  const { name } = useLocation().state || {}
  return (
    <StudentProgressReport
      // Remount per student so the previous student's name never shows.
      key={id}
      studentId={id}
      initialName={name || 'Student'}
      backTo="/admin"
      backLabel="Back to the admin console"
      backHoverClass="hover:text-slate-700"
      emptyTopicText="No topic data yet for this student."
      viewerRole="teacher"
    />
  )
}
