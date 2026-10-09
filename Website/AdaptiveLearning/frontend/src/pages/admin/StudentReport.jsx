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
    <>
    <p className="px-6 pt-4 text-xs text-gray-600 dark:text-gray-400">
      This is the student&rsquo;s own report, readings included, as their teacher sees it. Data flow and the
      Engine page show only whether data arrives and counts, never readings.
    </p>
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
    </>
  )
}
