import Heatmap from '../charts/Heatmap'
import Panel from './Panel'

const WEEKDAYS = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday',
  'Saturday', 'Sunday']

/** "9am", "2pm". */
function hourLabel(h) {
  if (h === 0) return '12am'
  if (h === 12) return '12pm'
  return h < 12 ? `${h}am` : `${h - 12}pm`
}

/**
 * Accuracy by weekday and hour, already bucketed school-local by the backend
 * (never converted here). Only worked days and hours get a row or column.
 */
export default function ClassTimeOfDay({ data, loading, onRetry }) {
  const cells = data?.cells || []
  const hours = data?.hours || []

  const weekdays = [...new Set(cells.map(c => c.weekday))].sort((a, b) => a - b)

  const byKey = new Map(cells.map(c => [`${c.weekday}:${c.hour}`, c]))

  return (
    <Panel
      title="When the class works"
      note={`Accuracy by weekday and hour of the school day, over the last ${data?.days || 0} days.`}
      loading={loading}
      failed={data?.retrieved === false}
      what="the time-of-day breakdown"
      onRetry={onRetry}
      empty={!cells.length}
      emptyNote="No questions answered in this range yet."
    >
      <Heatmap
        caption={`${data?.attempted || 0} questions across ${weekdays.length} days of the week.`}
        rowHeader="Day"
        columns={hours.map(h => ({ key: h, label: hourLabel(h) }))}
        rows={weekdays.map(wd => ({
          key: wd,
          label: WEEKDAYS[wd] ?? `Day ${wd}`,
          // Aligned here: the payload is sparse, and a missing cell is a real absence.
          cells: hours.map(h => byKey.get(`${wd}:${h}`) ?? null),
        }))}
      />
    </Panel>
  )
}
