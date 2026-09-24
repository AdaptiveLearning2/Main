import { offLabel, pct } from '../signals/SignalPanel'
import Panel from './Panel'
import ScaleNote from '../signals/ScaleNote'
import { combineScales } from '../../lib/scoreScale'

/**
 * Per-student signal averages for a class, against the class average.
 * A real `<table>` (it is the table, so no chart wrapper). The backend
 * withholds rows below its roster floor; there is nothing to hide client-side.
 */

/** Per-channel offLabel reasons; `retrieved` is applied in `cellLabel`. */
function reasons(summary) {
  return {
    eeg: {
      on: summary?.eeg_enabled !== false,
      revokedAt: summary?.eeg_revoked_at ?? null,
      consentRetrieved: summary?.consent_retrieved,
      samples: summary?.cognitive_samples,
    },
    heart: {
      on: summary?.heart_included !== false,
      revokedAt: summary?.heart_revoked_at ?? null,
      consentRetrieved: summary?.consent_retrieved,
      samples: summary?.heart_samples,
    },
  }
}

/**
 * Label for a cell with no number. Order: consent unreadable, then revoked
 * (beats an unread row, since consent is a separate query), then unread row,
 * then calibrating / no sensor.
 */
function cellLabel(summary, why) {
  const decidedByConsent = why.consentRetrieved === false || !why.on
  const unread = !decidedByConsent && summary?.retrieved === false
  return offLabel(unread ? { ...why, consentRetrieved: false } : why)
}

/**
 * The class mean of one field over students with a reading.
 * Unweighted on purpose (unlike the trend): each classmate is one comparison.
 */
function classMean(rows, field) {
  const values = rows
    .map(r => r.summary?.[field])
    .filter(v => typeof v === 'number')
  if (!values.length) return null
  return values.reduce((a, b) => a + b, 0) / values.length
}

/** How far from the class mean a value has to sit before it is worth a look. */
const OUTLIER_BAND = 0.15

function isOutlier(value, mean) {
  return typeof value === 'number' && mean !== null && Math.abs(value - mean) >= OUTLIER_BAND
}

export default function ClassSignalRoster({ data, loading, onRetry, hideSensors = false }) {
  const rows = data?.per_student || []
  // Any mix of score scales needs the caveat, outlier flag included.
  const scale = combineScales([{ score_scale: data?.score_scale }, ...rows.map(r => r.summary)])
  const withheld = data?.per_student === null && data?.class_size > 0
  const focusMean = classMean(rows, 'focus')

  const note = withheld
    ? `A per-student breakdown is withheld for classes smaller than ${data?.min_students ?? 5}`
      + ` students, where it would identify individuals. The class average above still applies.`
    : 'No signals recorded for this class in this range yet.'

  return (
    <Panel
      title="Per-student signals"
      note={hideSensors
        ? 'Sensor data is hidden by your view preference.'
        : `Compared against the class average. ${data?.summaries_retrieved === false
          ? 'Some averages could not be read.' : ''}`.trim()}
      loading={loading}
      failed={data?.retrieved === false}
      what="the per-student signals"
      onRetry={onRetry}
      empty={hideSensors || !rows.length}
      emptyNote={hideSensors
        ? 'Sensor data is hidden. Turn off "Hide sensor data" to see this table.'
        : note}
      className="lg:col-span-2"
    >
      <div className="overflow-x-auto">
        <ScaleNote scale={scale} what="These per-student figures" />
        <table className="w-full text-sm">
          <caption className="sr-only">
            Per-student signal averages for this class over the last {data?.days} days.
          </caption>
          <thead>
            <tr className="text-left text-gray-600 dark:text-gray-400">
              <th scope="col" className="py-2 pr-4 font-semibold">Student</th>
              <th scope="col" className="py-2 pr-4 font-semibold">Focus</th>
              <th scope="col" className="py-2 pr-4 font-semibold">Stress</th>
              <th scope="col" className="py-2 pr-4 font-semibold">Heart rate</th>
              {/* Days, not sessions: same rollup rows as the averages, same lifetime. */}
              <th scope="col" className="py-2 font-semibold">Days</th>
            </tr>
          </thead>
          <tbody>
            {rows.map(r => {
              const s = r.summary || {}
              const why = reasons(s)
              const outlier = isOutlier(s.focus, focusMean)
              return (
                <tr key={r.student_id}
                    className="border-t border-gray-100 dark:border-gray-800">
                  <th scope="row" className="py-2 pr-4 font-medium text-gray-900 dark:text-white">
                    {r.display_name}
                    {/* In words, not colour alone. */}
                    {outlier && (
                      <span className="ml-2 rounded-full bg-amber-100 dark:bg-amber-900/40
                                       px-2 py-0.5 text-[11px] font-semibold
                                       text-amber-800 dark:text-amber-200">
                        unlike the class
                      </span>
                    )}
                  </th>
                  <td className="py-2 pr-4 text-gray-900 dark:text-white">
                    {typeof s.focus === 'number' ? pct(s.focus) : cellLabel(s, why.eeg)}
                  </td>
                  <td className="py-2 pr-4 text-gray-900 dark:text-white">
                    {typeof s.stress === 'number' ? pct(s.stress) : cellLabel(s, why.eeg)}
                  </td>
                  <td className="py-2 pr-4 text-gray-900 dark:text-white">
                    {typeof s.heart_rate_bpm === 'number'
                      ? `${Math.round(s.heart_rate_bpm)} bpm`
                      : cellLabel(s, why.heart)}
                  </td>
                  {/* A dash, not 0, when the row was never read. */}
                  <td className="py-2 text-gray-900 dark:text-white">
                    {s.retrieved === false ? '—' : (s.days_recorded ?? 0)}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </Panel>
  )
}
