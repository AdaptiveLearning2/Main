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
      // The same panel the parent gets, framed for a teacher. The endpoint was
      // already role-neutral -- gated on relationship, not role -- so this was
      // a surface a teacher could reach and could not see.
      //
      // Behind the same switch as the charts, because the advice *is* sensor
      // data in prose: the rule-based list says "stress indicators ran high
      // this week" and "focus indicators were low this week", and the model
      // pass is handed the same averages. Left unconditional, Hide sensor data
      // took the tiles off screen and left a button that writes the numbers
      // back out as sentences. Hiding the whole panel rather than filtering
      // its lines is deliberate: the advice mixes topic accuracy with signal
      // readings and nothing downstream can separate them, and asking the
      // endpoint for a signal-free list would change the advice rather than
      // hide it.
      showStrategies={!hideSensors}
      // Behind the same switch, and for a stronger version of the same
      // reason: the strategies list mentions sensor readings in passing,
      // where this panel's whole job is to state them -- "Average focus is
      // 63%, and across the weeks with readings it has risen from 55% to
      // 63%". Left unconditional it would put the numbers the switch just
      // removed back on the page as sentences, under a heading naming the
      // charts that are no longer there.
      showChartSummary={!hideSensors}
      viewerRole="teacher"
    />
    </>
  )
}
