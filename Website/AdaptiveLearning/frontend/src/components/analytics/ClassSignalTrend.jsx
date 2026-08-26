import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, Legend } from 'recharts'
import AccessibleChart from '../charts/AccessibleChart'
import { asPercent } from '../charts/describeSeries'
import Panel from './Panel'

/**
 * The class's signal averages per school day.
 *
 * The payload is one row per (day, channel), because the three channels are
 * aggregated separately and a day can have any subset of them. The chart wants
 * one row per day, so the channels are folded together here — a day with only
 * cognitive rows still appears, carrying nulls for the rest.
 *
 * Days with nothing recorded are absent from the payload and stay absent here.
 * `connectNulls={false}` is what makes that a visible gap rather than a line
 * drawn straight through a fortnight nobody was in.
 */

/** One row per day, from the payload's row-per-(day, channel). */
function foldByDay(series) {
  const byDay = new Map()
  for (const r of series || []) {
    const row = byDay.get(r.day) || { day: r.day, label: String(r.day).slice(5) }
    // Each channel contributes only its own metrics, so a heart row cannot
    // overwrite a cognitive row's focus with the undefined it carries.
    if (r.channel === 'cognitive') {
      row.avg_focus = r.avg_focus
      row.avg_stress = r.avg_stress
      row.avg_engagement = r.avg_engagement
    } else if (r.channel === 'heart') {
      row.avg_heart_rate_bpm = r.avg_heart_rate_bpm
      row.avg_rmssd_ms = r.avg_rmssd_ms
    }
    // Students are counted per channel, so the day's coverage is the widest
    // channel rather than the sum — a student with both a headband and a
    // camera appears in two rows and is still one student.
    row.student_count = Math.max(row.student_count || 0, r.student_count || 0)
    byDay.set(r.day, row)
  }
  return [...byDay.values()].sort((a, b) => String(a.day).localeCompare(String(b.day)))
}

export default function ClassSignalTrend({ data, loading, onRetry, hideSensors = false }) {
  const rows = foldByDay(data?.series)

  // Whether a series is drawn at all, so the column spec can mirror it. A
  // column naming a line the chart does not draw reads a screen-reader user a
  // series no sighted reader can see — the defect this codebase has shipped
  // twice, and the reason the two are derived from one condition here.
  const hasCognitive = rows.some(r => typeof r.avg_focus === 'number')
  const hasHeart = !hideSensors && rows.some(r => typeof r.avg_heart_rate_bpm === 'number')

  // Ratios are stored 0..1 and read as percentages, so they carry
  // `scale: asPercent` with a `%` unit. Heart rate is already in its own unit
  // and takes no scale — combining the two would announce 6700%.
  const COLUMNS = [
    ...(hasCognitive ? [
      { key: 'avg_focus', label: 'Focus', unit: '%', scale: asPercent },
      { key: 'avg_stress', label: 'Stress', unit: '%', scale: asPercent },
      { key: 'avg_engagement', label: 'Engagement', unit: '%', scale: asPercent },
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
      <div className="h-64">
        <AccessibleChart
          headline={headline} rows={rows} rowKey="label" rowLabel="Day"
          columns={COLUMNS}
        >
          <LineChart data={rows} margin={{ top: 8, right: 8, left: -16, bottom: 0 }}>
            <CartesianGrid strokeDasharray="3 3" className="stroke-gray-200 dark:stroke-gray-700" />
            <XAxis dataKey="label" tick={{ fontSize: 11 }} />
            {/* Ratios plotted raw against a 0..1 domain, formatted to percent
                on the axis and the tooltip. The column spec scales instead,
                which is why it carries `asPercent` and this does not. */}
            <YAxis yAxisId="ratio" domain={[0, 1]} tick={{ fontSize: 11 }}
              tickFormatter={v => `${Math.round(v * 100)}%`} />
            {hasHeart && (
              <YAxis yAxisId="bpm" orientation="right" tick={{ fontSize: 11 }} unit=" bpm" />
            )}
            <Tooltip formatter={(v, name) => (name === 'Heart rate'
              ? [`${Math.round(v)} bpm`, name]
              : [`${Math.round(v * 100)}%`, name])} />
            <Legend wrapperStyle={{ fontSize: 12 }} />
            {hasCognitive && <>
              <Line yAxisId="ratio" type="monotone" dataKey="avg_focus" name="Focus"
                stroke="#7c3aed" strokeWidth={2} dot={{ r: 3 }} connectNulls={false} />
              <Line yAxisId="ratio" type="monotone" dataKey="avg_stress" name="Stress"
                stroke="#e11d48" strokeWidth={2} dot={{ r: 3 }} connectNulls={false} />
              <Line yAxisId="ratio" type="monotone" dataKey="avg_engagement" name="Engagement"
                stroke="#0891b2" strokeWidth={2} dot={{ r: 3 }} connectNulls={false} />
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
