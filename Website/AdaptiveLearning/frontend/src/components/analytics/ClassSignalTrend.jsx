import { useId } from 'react'
import { LineChart, Line, XAxis, YAxis, CartesianGrid, Legend } from 'recharts'
import ChartTooltip from '../charts/ChartTooltip'
import AccessibleChart from '../charts/AccessibleChart'
import { asPercent } from '../charts/describeSeries'
import Panel from './Panel'
import ScaleNote from '../signals/ScaleNote'
import { calmRatio } from '../../lib/signalFormat'
import { MEASURES } from '../../lib/signalGlossary'
import { LINE_LEGEND } from '../charts/legendProps'

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
      // EEG stress is `1 - calm`: drawn as calm; null stays null.
      row.avg_calm = calmRatio(r.avg_stress)
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
  const syncId = useId()

  // Lines and columns share these conditions. `hideSensors` gates every series.
  const hasCognitive = !hideSensors && rows.some(r => typeof r.avg_focus === 'number')
  const hasHeart = !hideSensors && rows.some(r => typeof r.avg_heart_rate_bpm === 'number')

  // One chart per unit, never two y-axes. Ratios scale to percent; heart rate takes no scale.
  const CHARTS = [
    ...(hasCognitive ? [{ id: 'pct', title: 'Focus and calm', domain: [0, 1],
      tick: v => `${Math.round(v * 100)}%`, tip: v => `${Math.round(v * 100)}%`, series: [
        { key: 'avg_focus', label: MEASURES.focus.name, unit: '%', scale: asPercent, colour: '#6366f1' },
        // Dashed, as on the reports: focus and calm are close for tritan vision.
        { key: 'avg_calm', label: MEASURES.calm.name, unit: '%', scale: asPercent, colour: '#0d9488', dash: '6 3' },
        // No engagement: it is the focus index.
      ] }] : []),
    ...(hasHeart ? [{ id: 'bpm', title: 'Heart rate', domain: ['auto', 'auto'],
      tick: v => `${Math.round(v)}`, tip: v => `${Math.round(v)} bpm`, series: [
        { key: 'avg_heart_rate_bpm', label: MEASURES.heart_rate_bpm.name, unit: ' bpm', colour: '#a855f7' },
      ] }] : []),
  ]

  const days = rows.length
  const headline = title => `Class ${title.toLowerCase()} across ${days} ${days === 1 ? 'day' : 'days'}`
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
      empty={!rows.length || !CHARTS.length}
      emptyNote={hideSensors
        ? 'Sensor data is hidden. Turn off "Hide sensor data" to see this chart.'
        : 'No signals recorded for this class in this range yet.'}
      className="lg:col-span-2"
    >
      {/* Score-scale caveat, only beside a drawn focus/calm line. */}
      {hasCognitive && (
        <ScaleNote scale={data?.score_scale} what="This class's averages" />
      )}
      <div className="space-y-3">
        {CHARTS.map(c => (
          <div key={c.id}>
            <p className="text-xs font-bold text-gray-600 dark:text-gray-400 mb-1">{c.title}</p>
            <AccessibleChart className="h-48"
              headline={headline(c.title)} rows={rows} rowKey="label" rowLabel="Day"
              columns={c.series.map(({ key, label, unit, scale }) => ({ key, label, unit, scale }))}
            >
              <LineChart data={rows} syncId={syncId} margin={{ top: 8, right: 8, left: -16, bottom: 0 }}>
                <CartesianGrid strokeDasharray="3 3" className="stroke-gray-200 dark:stroke-gray-700" />
                <XAxis dataKey="label" tick={{ fontSize: 11 }} />
                {/* Raw values on the axis; the column spec scales for the table instead. */}
                <YAxis domain={c.domain} tick={{ fontSize: 11 }} tickFormatter={c.tick} />
                <ChartTooltip formatter={(v, name) => [c.tip(v), name]} />
                {c.series.length > 1 && <Legend {...LINE_LEGEND} wrapperStyle={{ fontSize: 12 }} />}
                {/* Filled dots: Recharts' default white centre reads as hollow. */}
                {c.series.map(s => (
                  <Line key={s.key} type="monotone" dataKey={s.key} name={s.label}
                    stroke={s.colour} strokeWidth={2} strokeDasharray={s.dash}
                    dot={{ r: 3, fill: s.colour, strokeWidth: 0 }} connectNulls={false} />
                ))}
              </LineChart>
            </AccessibleChart>
          </div>
        ))}
      </div>
    </Panel>
  )
}
