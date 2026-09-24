import { useState } from 'react'
import { useParams, useLocation } from 'react-router-dom'
import StudentProgressReport from '../../components/reports/StudentProgressReport'
import HideSensorDataToggle from '../../components/common/HideSensorDataToggle'
import { readHideSensorData, writeHideSensorData } from '../../lib/viewPrefs'

export default function StudentReport() {
  const [hideSensors, setHideSensors] = useState(readHideSensorData)
  const { id } = useParams()
  const location = useLocation()
  const { name, classId, className } = location.state || {}

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
      // Remount per student so the previous student's name never shows.
      key={id}
      studentId={id}
      // From router state, so a name shows before the report loads.
      initialName={name || 'Student'}
      backTo={backTo}
      backLabel={backLabel}
      backHoverClass="hover:text-violet-600"
      emptyTopicText="No topic data yet — this student hasn't used AI Adaptive mode."
      showSignals={!hideSensors}
      // Hidden with the charts: the advice is sensor data in prose, and its
      // lines can't be separated from topic accuracy.
      showStrategies={!hideSensors}
      // Hidden with the charts: this panel states the sensor numbers outright.
      showChartSummary={!hideSensors}
      viewerRole="teacher"
    />
    </>
  )
}
