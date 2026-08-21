import { useState } from 'react'
import { useParams, useLocation } from 'react-router-dom'
import StudentProgressReport from '../../components/reports/StudentProgressReport'
import HideSensorDataToggle from '../../components/common/HideSensorDataToggle'
import { readHideSensorData, writeHideSensorData } from '../../lib/viewPrefs'

export default function StudentReport() {
  // Renders the sensor-hide toggle from lib/viewPrefs.js.
  const [hideSensors, setHideSensors] = useState(readHideSensorData)
  const { id } = useParams()
  const location = useLocation()
  const { name, classId, className } = location.state || {}

  // Link back to the class the teacher came from, or the class list otherwise.
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
      // Remount on a new student id so the heading doesn't show the previous
      // student's name until the fetch resolves.
      key={id}
      studentId={id}
      // Seeded from router state so the heading shows a name right away,
      // before the weekly report loads. Falls back to 'Student' if neither has one.
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
