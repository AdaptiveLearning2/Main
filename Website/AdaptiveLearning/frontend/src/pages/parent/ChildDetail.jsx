import { useCallback } from 'react'
import { useParams } from 'react-router-dom'
import { apiFetch } from '../../lib/api'
import StudentProgressReport from '../../components/reports/StudentProgressReport'

export default function ChildDetail() {
  const { id } = useParams()

  // Report-independent name source: the child's name comes from the children
  // list, so the heading survives a weekly-report failure. Memoised so it stays
  // stable across renders (the report runs it inside a studentId-keyed effect).
  const nameFetch = useCallback(
    () => apiFetch('/api/parent/children')
      .then(children => children.find(c => c.user_id === id)?.name || null),
    [id],
  )

  return (
    <StudentProgressReport
      // Remount on a new child id so the heading re-seeds from the name source
      // instead of showing the previous child's name until the fetch resolves.
      key={id}
      studentId={id}
      initialName="Child"
      backTo="/parent"
      backLabel="Back to Dashboard"
      backHoverClass="hover:text-emerald-600"
      emptyTopicText="No topic data yet — your child hasn't used AI Adaptive mode."
      nameFetch={nameFetch}
    />
  )
}
