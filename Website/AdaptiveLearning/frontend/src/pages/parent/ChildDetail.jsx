import { useCallback } from 'react'
import { useParams } from 'react-router-dom'
import { apiFetch } from '../../lib/api'
import StudentProgressReport from '../../components/reports/StudentProgressReport'

export default function ChildDetail() {
  const { id } = useParams()

  // Name comes from the children list, not the report, so the heading survives
  // a weekly-report failure. include_face=false because this call only wants
  // a name, not facial data for the whole family.
  const nameFetch = useCallback(
    () => apiFetch('/api/parent/children?include_face=false')
      .then(children => children.find(c => c.user_id === id)?.name || null),
    [id],
  )

  return (
    <StudentProgressReport
      // Remount on a new child id so the heading doesn't keep showing the
      // previous child's name until the fetch resolves.
      key={id}
      studentId={id}
      initialName="Child"
      backTo="/parent"
      backLabel="Back to Dashboard"
      backHoverClass="hover:text-emerald-600"
      emptyTopicText="No topic data yet — your child hasn't used AI Adaptive mode."
      nameFetch={nameFetch}
      // Parent route only: strategies are written for someone at home with
      // their child, not for the teacher page.
      showStrategies
    />
  )
}
