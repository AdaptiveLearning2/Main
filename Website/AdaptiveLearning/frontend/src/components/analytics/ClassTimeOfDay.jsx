import Heatmap from '../charts/Heatmap'
import Panel from './Panel'

const WEEKDAYS = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday',
  'Saturday', 'Sunday']

/** "9am", "2pm" — the hour a teacher would name, not a 24-hour column head. */
function hourLabel(h) {
  if (h === 0) return '12am'
  if (h === 12) return '12pm'
  return h < 12 ? `${h}am` : `${h - 12}pm`
}

/**
 * When in the week a class works, and how well it does at each hour.
 *
 * Weekday and hour both come from the school-local date the backend already
 * bucketed on, so nothing here converts a timezone. Doing it in the browser
 * would resolve the hour against the *viewer's* clock — a teacher marking from
 * home in another timezone would see the school day shifted.
 *
 * Only hours the class has actually worked in get a column. Midnight to
 * midnight is 168 cells of which a school uses perhaps thirty, and a wall of
 * blanks with three squares in it is not a finding.
 */
export default function ClassTimeOfDay({ data, loading, onRetry }) {
  const cells = data?.cells || []
  const hours = data?.hours || []

  // Only the days that were actually worked, for the same reason as the hours:
  // a permanently empty Saturday row teaches a reader to skip the table.
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
          // Aligned to `hours` by construction here rather than by the server,
          // because this grid is sparse — the payload carries only the cells
          // that exist, and an hour a given day never used is a real absence.
          cells: hours.map(h => byKey.get(`${wd}:${h}`) ?? null),
        }))}
      />
    </Panel>
  )
}
