import { useState } from 'react'
import { useParams, useLocation } from 'react-router-dom'
import StudentProgressReport from '../../components/reports/StudentProgressReport'
import HideSensorDataToggle from '../../components/common/HideSensorDataToggle'
import { readHideSensorData, writeHideSensorData } from '../../lib/viewPrefs'

export default function StudentReport() {
  // One of the two surfaces the teacher's display filter covers, and it renders
  // the switch -- a control that silently changed a page it is absent from
  // would be worse than one with a stated edge. See lib/viewPrefs.js.
  const [hideSensors, setHideSensors] = useState(readHideSensorData)
  const { id } = useParams()
  const location = useLocation()
  const { name, classId, className } = location.state || {}

  // Return to the specific class the teacher came from when we know it, so the
  // back link keeps context; fall back to the class list on a direct visit.
  const backTo    = classId ? `/teacher/classes/${classId}` : '/teacher/classes'
  const backLabel = className ? `Back to ${className}` : classId ? 'Back to Class' : 'Back to Classes'

  return (
    <>
      <div className="flex justify-end px-6 pt-6">
        <HideSensorDataToggle
          hidden={hideSensors}
          onChange={next => { setHideSensors(next); writeHideSensorData(next) }} />
      </div>
      <StudentProgressReport
      // Remount on a new student id so the heading re-seeds from initialName
      // instead of showing the previous student's name until a fetch resolves.
      key={id}
      studentId={id}
      // Seeded from router state (passed by the class roster) so the heading
      // shows the real name immediately, even if the weekly-report is slow or
      // fails; the report's student_name confirms it once loaded. On a cold
      // direct visit there is no router state (name falls back to 'Student') and
      // no class id, so the weekly-report's student_name is the only name source
      // -- if it fails the heading stays 'Student'.
      initialName={name || 'Student'}
      backTo={backTo}
      backLabel={backLabel}
      backHoverClass="hover:text-violet-600"
      emptyTopicText="No topic data yet — this student hasn't used AI Adaptive mode."
      showSignals={!hideSensors}
    />
    </>
  )
}
