import { LineChart, Line, XAxis, YAxis, CartesianGrid } from 'recharts'
import ChartTooltip from '../charts/ChartTooltip'
import AccessibleChart from '../charts/AccessibleChart'
import Panel from './Panel'

/**
 * Class accuracy per school day.
 *
 * Days with no answers are present in the payload and stay present here, drawn
 * as a gap. A dropped day renders as the days either side sitting adjacent, so
 * a week of half-term reads as an unbroken run of lessons.
 *
 * `connectNulls={false}` is what makes that gap visible rather than a straight
 * line drawn through it — the line would otherwise interpolate a week nobody
 * was in.
 */
export default function ClassAccuracyTrend({ data, loading, onRetry }) {
  const days = data?.days || []

  // Pre-scaled to percent on the way in, so the column spec below carries a
  // `%` unit and no `scale`. The two ways to reach a percentage must not be
  // combined: a `scale: asPercent` on top of this would announce 6700%.
  const rows = days.map(d => ({
    label: d.day.slice(5),
    accuracy: typeof d.accuracy === 'number' ? d.accuracy * 100 : null,
    attempted: d.attempted,
  }))

  // Mirrors the series the chart actually draws, and only those. `attempted`
  // was a column here first and is deliberately gone: nothing plots it, so a
  // screen-reader user would have been read a series no sighted reader can
  // see. That is the mistake CLAUDE.md records having shipped twice, and it is
  // invisible on screen by construction — the count lives in the headline
  // instead, where it is available to both readers.
  const COLUMNS = [{ key: 'accuracy', label: 'Accuracy', unit: '%' }]

  const withData = days.filter(d => d.attempted).length
  const headline = `Class accuracy across ${days.length} days, from ${data?.attempted || 0} questions answered on ${withData} of them.`

  return (
    <Panel
      title="Accuracy over time"
      note={`Every day in range, bucketed at the school's timezone (${data?.timezone || 'UTC'}).`}
      loading={loading}
      failed={data?.retrieved === false}
      what="the accuracy trend"
      onRetry={onRetry}
      empty={!withData}
      emptyNote="No questions answered in this range yet."
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
            <YAxis domain={[0, 100]} tick={{ fontSize: 11 }} unit="%" />
            <ChartTooltip formatter={v => [`${Math.round(v)}%`, 'Accuracy']} />
            <Line type="monotone" dataKey="accuracy" stroke="#7c3aed" strokeWidth={2}
              // A single day of data is a dot, not a line with no length.
              dot={{ r: 3 }} connectNulls={false} />
          </LineChart>
        </AccessibleChart>
      </div>
    </Panel>
  )
}
