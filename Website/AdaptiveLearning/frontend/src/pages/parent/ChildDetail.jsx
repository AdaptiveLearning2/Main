import { useCallback } from 'react'
import { useParams } from 'react-router-dom'
import { apiFetch } from '../../lib/api'
import StudentProgressReport from '../../components/reports/StudentProgressReport'

export default function ChildDetail() {
  const { id } = useParams()

  // Name from the children list so the heading survives a report failure;
  // include_face=false since only the name is wanted.
  const nameFetch = useCallback(
    () => apiFetch('/api/parent/children?include_face=false')
      .then(children => children.find(c => c.user_id === id)?.name || null),
    [id],
  )

  return (
    <StudentProgressReport
      // Remount per child so the previous child's name never shows.
      key={id}
      studentId={id}
      initialName="Child"
      backTo="/parent"
      backLabel="Back to Dashboard"
      backHoverClass="hover:text-emerald-600"
      emptyTopicText="No topic data yet — your child hasn't used AI Adaptive mode."
      nameFetch={nameFetch}
      showStrategies
      // Ungated: the parent surface has no sensor-data switch.
      showChartSummary
    />
  )
}
