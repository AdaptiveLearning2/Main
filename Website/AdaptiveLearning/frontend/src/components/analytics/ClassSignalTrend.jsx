import { LineChart, Line, XAxis, YAxis, CartesianGrid, Legend } from 'recharts'
import ChartTooltip from '../charts/ChartTooltip'
import AccessibleChart from '../charts/AccessibleChart'
import { asPercent } from '../charts/describeSeries'
import Panel from './Panel'
import ScaleNote from '../signals/ScaleNote'

/**
 * The class's signal averages per school day. Days with nothing recorded stay
 * absent, drawn as gaps (`connectNulls={false}`).
 */

/** One row per day, from the payload's row-per-(day, channel). */
function foldByDay(series) {
  const byDay = new Map()
  for (const r of series || []) {
    const row = byDay.get(r.day) || { day: r.day, label: String(r.day).slice(5) }
    // Each channel writes only its own metrics.
    if (r.channel === 'cognitive') {
      row.avg_focus = r.avg_focus
      row.avg_stress = r.avg_stress
    } else if (r.channel === 'heart') {
      row.avg_heart_rate_bpm = r.avg_heart_rate_bpm
      row.avg_rmssd_ms = r.avg_rmssd_ms
    }
    // Max, not sum: one student can appear in two channels.
    row.student_count = Math.max(row.student_count || 0, r.student_count || 0)
    byDay.set(r.day, row)
  }
  return [...byDay.values()].sort((a, b) => String(a.day).localeCompare(String(b.day)))
}

export default function ClassSignalTrend({ data, loading, onRetry, hideSensors = false }) {
  const rows = foldByDay(data?.series)

  // Lines and columns share these conditions. `hideSensors` gates every series.
  const hasCognitive = !hideSensors && rows.some(r => typeof r.avg_focus === 'number')
  const hasHeart = !hideSensors && rows.some(r => typeof r.avg_heart_rate_bpm === 'number')

  // Ratios scale to percent; heart rate takes no scale.
  const COLUMNS = [
    ...(hasCognitive ? [
      { key: 'avg_focus', label: 'Focus', unit: '%', scale: asPercent },
      { key: 'avg_stress', label: 'Stress', unit: '%', scale: asPercent },
      // No engagement: it is the focus index.
    ] : []),
    ...(hasHeart ? [{ key: 'avg_heart_rate_bpm', label: 'Heart rate', unit: ' bpm' }] : []),
  ]

  const days = rows.length
  const headline = `Class signal averages across ${days} ${days === 1 ? 'day' : 'days'}`
    + ` with recordings, bucketed at the school's timezone (${data?.timezone || 'UTC'}).`

  return (
    <Panel
      title="Class signals over time"
      note={hideSensors
        ? 'Sensor data is hidden by your view preference.'
        : "Averaged across the class, weighted by how much each student recorded."}
      loading={loading}
      failed={data?.retrieved === false}
      what="the class signal trend"
      onRetry={onRetry}
      empty={!rows.length || !COLUMNS.length}
      emptyNote={hideSensors
        ? 'Sensor data is hidden. Turn off "Hide sensor data" to see this chart.'
        : 'No signals recorded for this class in this range yet.'}
      className="lg:col-span-2"
    >
      {/* Score-scale caveat, only beside a drawn focus/stress line. */}
      {hasCognitive && (
        <ScaleNote scale={data?.score_scale} what="This class's averages" />
      )}
      <div className="h-64">
        <AccessibleChart
          headline={headline} rows={rows} rowKey="label" rowLabel="Day"
          columns={COLUMNS}
        >
          <LineChart data={rows} margin={{ top: 8, right: 8, left: -16, bottom: 0 }}>
            <CartesianGrid strokeDasharray="3 3" className="stroke-gray-200 dark:stroke-gray-700" />
            <XAxis dataKey="label" tick={{ fontSize: 11 }} />
            {/* Raw 0..1 domain, formatted as percent; the column spec scales instead. */}
            <YAxis yAxisId="ratio" domain={[0, 1]} tick={{ fontSize: 11 }}
              tickFormatter={v => `${Math.round(v * 100)}%`} />
            {hasHeart && (
              <YAxis yAxisId="bpm" orientation="right" tick={{ fontSize: 11 }} unit=" bpm" />
            )}
            <ChartTooltip formatter={(v, name) => (name === 'Heart rate'
              ? [`${Math.round(v)} bpm`, name]
              : [`${Math.round(v * 100)}%`, name])} />
            <Legend wrapperStyle={{ fontSize: 12 }} />
            {hasCognitive && <>
              <Line yAxisId="ratio" type="monotone" dataKey="avg_focus" name="Focus"
                stroke="#7c3aed" strokeWidth={2} dot={{ r: 3 }} connectNulls={false} />
              <Line yAxisId="ratio" type="monotone" dataKey="avg_stress" name="Stress"
                stroke="#e11d48" strokeWidth={2} dot={{ r: 3 }} connectNulls={false} />
            </>}
            {hasHeart && (
              <Line yAxisId="bpm" type="monotone" dataKey="avg_heart_rate_bpm" name="Heart rate"
                stroke="#ea580c" strokeWidth={2} dot={{ r: 3 }} connectNulls={false} />
            )}
          </LineChart>
        </AccessibleChart>
      </div>
    </Panel>
  )
}
