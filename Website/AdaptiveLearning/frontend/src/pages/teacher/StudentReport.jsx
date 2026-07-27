import { useParams, useLocation } from 'react-router-dom'
import StudentProgressReport from '../../components/reports/StudentProgressReport'

export default function StudentReport() {
  const { id } = useParams()
  const location = useLocation()

  return (
    <StudentProgressReport
      studentId={id}
      // Seeded from router state (passed by the class roster) so the heading
      // shows the real name immediately, even if the weekly-report is slow or
      // fails; the report's student_name confirms it once loaded.
      initialName={location.state?.name || 'Student'}
      backTo="/teacher/classes"
      backLabel="Back to Classes"
      backHoverClass="hover:text-violet-600"
      emptyTopicText="No topic data yet — this student hasn't used AI Adaptive mode."
    />
  )
}
