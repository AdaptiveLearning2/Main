import { BarChart, Bar, XAxis, YAxis, CartesianGrid } from 'recharts'
import AccessibleChart from '../charts/AccessibleChart'
import ChartTooltip from '../charts/ChartTooltip'
import { axisLabelFill } from '../charts/chartTooltipStyles'
import { useTheme } from '../../context/ThemeContext'
import { fmtDate } from '../../lib/dates'
import Panel from './Panel'

/**
 * Whether this student answers better when the headband reads focused.
 * States: consent withdrawn (with date), consent unreadable, too few pairs
 * (buckets drawn, correlation withheld), enough pairs (both).
 */
export default function FocusAccuracy({ data, loading, onRetry }) {
  // Theme-aware axis label fill; the library default fails AA. No provider reads as light.
  const labelFill = axisLabelFill(!!useTheme()?.dark)
  const off = data?.eeg_enabled === false
  const consentUnknown = data?.consent_retrieved === false
  const buckets = data?.buckets || []

  const rows = buckets.map(b => ({
    label: `${Math.round((b.focus_low ?? 0) * 100)}–${Math.round((b.focus_high ?? 0) * 100)}%`,
    accuracy: typeof b.accuracy === 'number' ? b.accuracy * 100 : null,
  }))

  const COLUMNS = [{ key: 'accuracy', label: 'Accuracy', unit: '%' }]

  const r = data?.correlation
  const headline = `Answer accuracy at each focus level, from ${data?.pairs || 0} answers with a focus reading.`

  let verdict
  if (typeof r === 'number') {
    // In words too; bands deliberately cautious at the top.
    const strength = Math.abs(r) < 0.2 ? 'little or no'
      : Math.abs(r) < 0.4 ? 'a weak' : 'a moderate'
    // Direction from the rounded figure, so `r = 0.00` never reads as signed.
    const rounded = Number(r.toFixed(2))
    const direction = rounded === 0 ? 'No direction'
      : rounded > 0 ? 'Positive' : 'Negative'
    verdict = `${direction}: ${strength} relationship (r = ${r.toFixed(2)}) over ${data.pairs} answers.`
  } else if (data?.sufficient) {
    // Null `corr()` with enough pairs: no variance (e.g. every answer correct).
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
          {/* Both axes named: both are percentages. */}
          <BarChart data={rows} margin={{ top: 8, right: 8, left: 4, bottom: 16 }}>
            <CartesianGrid strokeDasharray="3 3" className="stroke-gray-200 dark:stroke-gray-700" />
            <XAxis dataKey="label" tick={{ fontSize: 11 }}
                   label={{ value: 'Focus reading from the headband', position: 'insideBottom', offset: -10, fontSize: 11, fill: labelFill }} />
            <YAxis domain={[0, 100]} tick={{ fontSize: 11 }} unit="%"
                   label={{ value: 'Answers correct', angle: -90, position: 'insideLeft', offset: 14, fontSize: 11, fill: labelFill }} />
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
