import { BarChart, Bar, XAxis, YAxis, CartesianGrid } from 'recharts'
import AccessibleChart from '../charts/AccessibleChart'
import ChartTooltip from '../charts/ChartTooltip'
import { fmtDate } from '../../lib/dates'
import Panel from './Panel'

/**
 * Whether this student answers better when the headband reads focused.
 *
 * Four states, and the two in the middle are the ones worth keeping apart:
 *
 *   - EEG consent withdrawn  → says so, with the date. The join was never run.
 *   - consent unreadable     → "unavailable". "They turned it off" is a claim
 *                              a failed read has not earned.
 *   - read, too few pairs    → the buckets are drawn and the correlation is
 *                              withheld. r over a dozen answers is noise, and
 *                              it renders as a single objective-looking number
 *                              with no visible denominator.
 *   - read, enough pairs     → the correlation, alongside the buckets.
 *
 * The bars are shown below the threshold on purpose: a bar chart carries its
 * own sample sizes in a way a scalar cannot, so a reader can see that the
 * left-hand bin rests on four answers.
 */
export default function FocusAccuracy({ data, loading, onRetry }) {
  const off = data?.eeg_enabled === false
  const consentUnknown = data?.consent_retrieved === false
  const buckets = data?.buckets || []

  const rows = buckets.map(b => ({
    label: `${Math.round((b.focus_low ?? 0) * 100)}–${Math.round((b.focus_high ?? 0) * 100)}%`,
    accuracy: typeof b.accuracy === 'number' ? b.accuracy * 100 : null,
  }))

  // One series drawn, one column. Same rule as the accuracy trend.
  const COLUMNS = [{ key: 'accuracy', label: 'Accuracy', unit: '%' }]

  const r = data?.correlation
  const headline = `Answer accuracy at each focus level, from ${data?.pairs || 0} answers with a focus reading.`

  let verdict
  if (typeof r === 'number') {
    // Described in words as well as reported, because a bare coefficient is
    // read as a grade by anyone who does not work with them daily. The bands
    // are conventional and deliberately cautious at the top.
    const strength = Math.abs(r) < 0.2 ? 'little or no'
      : Math.abs(r) < 0.4 ? 'a weak' : 'a moderate'
    // Direction is only claimed where there is one. `corr()` returns an exact
    // 0 readily — it is the answer whenever the two are perfectly unrelated —
    // and `r > 0 ? … : 'Negative'` labelled that "Negative: little or no
    // relationship", which contradicts itself in the same sentence and points
    // a teacher at a trend that is not there. Rounding makes the reachable set
    // wider than exact zero, too: anything under 0.005 prints as `r = 0.00`,
    // so a signed label would disagree with the figure printed beside it.
    const rounded = Number(r.toFixed(2))
    const direction = rounded === 0 ? 'No direction'
      : rounded > 0 ? 'Positive' : 'Negative'
    verdict = `${direction}: ${strength} relationship (r = ${r.toFixed(2)}) over ${data.pairs} answers.`
  } else if (data?.sufficient) {
    // `corr()` answers null when an input has no variance — every answer
    // correct, say. Enough data, no coefficient, which is not the same as
    // not enough data.
    verdict = `No coefficient could be computed from these ${data.pairs} answers.`
  } else {
    verdict = `Too few answers with a focus reading to report a correlation — ${data?.pairs || 0} of the ${data?.min_pairs || 0} needed.`
  }

  if (off || consentUnknown) {
    return (
      <Panel title="Focus and accuracy" loading={loading}
        note="Whether answers are more often right when the headband reads focused.">
        <p className="py-12 text-center text-sm text-gray-600 dark:text-gray-400">
          {consentUnknown
            ? 'Unavailable — we could not read this student’s consent settings.'
            : `Headband recording is off${fmtDate(data?.eeg_revoked_at) ? ` since ${fmtDate(data.eeg_revoked_at)}` : ''}, so no focus readings were used.`}
        </p>
      </Panel>
    )
  }

  return (
    <Panel
      title="Focus and accuracy"
      note="Whether answers are more often right when the headband reads focused."
      loading={loading}
      failed={data?.retrieved === false}
      what="the focus comparison"
      onRetry={onRetry}
      empty={!buckets.length}
      emptyNote="No answers yet with a focus reading recorded at the same time."
    >
      <p className="mb-3 text-sm font-bold text-gray-900 dark:text-white">{verdict}</p>
      <div className="h-56">
        <AccessibleChart
          headline={headline} rows={rows} rowKey="label" rowLabel="Focus"
          columns={COLUMNS}
        >
          {/* Both axes named. Without them the chart read as three purple
              bars over three percentages, and which percentage was the
              headband's and which the answers' was not on the picture. */}
          <BarChart data={rows} margin={{ top: 8, right: 8, left: 4, bottom: 16 }}>
            <CartesianGrid strokeDasharray="3 3" className="stroke-gray-200 dark:stroke-gray-700" />
            <XAxis dataKey="label" tick={{ fontSize: 11 }}
                   label={{ value: 'Focus reading from the headband', position: 'insideBottom', offset: -10, fontSize: 11 }} />
            <YAxis domain={[0, 100]} tick={{ fontSize: 11 }} unit="%"
                   label={{ value: 'Answers correct', angle: -90, position: 'insideLeft', offset: 14, fontSize: 11 }} />
            <ChartTooltip formatter={v => [`${Math.round(v)}%`, 'Answers correct']}
                          labelFormatter={l => `Focus ${l}`} />
            <Bar dataKey="accuracy" fill="#7c3aed" radius={[4, 4, 0, 0]} />
          </BarChart>
        </AccessibleChart>
      </div>
      <p className="mt-2 text-xs text-gray-600 dark:text-gray-400">
        A relationship here is not a cause. A student may focus harder on
        questions they already find easy.
      </p>
    </Panel>
  )
}
