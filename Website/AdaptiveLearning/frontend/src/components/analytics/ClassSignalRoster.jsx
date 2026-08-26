import { offLabel, pct } from '../signals/SignalPanel'
import Panel from './Panel'

/**
 * Per-student signal averages for a class, against the class average.
 *
 * A real `<table>` rather than an `AccessibleChart`, for the reason `Heatmap`
 * is: that wrapper exists because Recharts emits a bare `<svg>` with nothing a
 * screen reader can walk, so it pairs the picture with an `sr-only` table. This
 * *is* the table — `<th scope>` headers already let a reader ask for one cell
 * by its two headings, which an sr-only copy could not improve on.
 *
 * The backend withholds these rows entirely below its own floor, so there is
 * nothing to hide here — only to render when they arrive. A client-side hide
 * would leave them in the payload for anyone reading it.
 */

/** Every tile's reason, from the one summary payload.
 *
 * Kept per channel rather than one per row: a student can have EEG on and the
 * camera off, and a single reason would explain the wrong null.
 */
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

/** The class mean of one field, over the students who have a reading.
 *
 * Unweighted, deliberately, and it is a different number from the trend's
 * weighted average: this one answers "is this student unusual among their
 * classmates", where each classmate is one comparison whatever their session
 * length. The trend answers "how did the class do", where a long session is
 * more of the class's day. Using the weighted figure here would flag a student
 * for sitting next to someone who recorded all afternoon.
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
  const withheld = data?.per_student === null && data?.class_size > 0
  const focusMean = classMean(rows, 'focus')

  // The floor is the backend's, and the note says so in the roster's own terms
  // rather than as a generic empty state — a teacher looking for a breakdown
  // that is not there is owed the reason, not a shrug.
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
              {/* Days recorded, not sessions: it comes from the same rows the
                  averages do, so the whole row survives the end-of-year expiry
                  together. A session count would come from another table with
                  a different lifetime. */}
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
                    {/* Spelled out, not left to colour alone: the flag is the
                        whole point of the row and colour is not available to
                        every reader. */}
                    {outlier && (
                      <span className="ml-2 rounded-full bg-amber-100 dark:bg-amber-900/40
                                       px-2 py-0.5 text-[11px] font-semibold
                                       text-amber-800 dark:text-amber-200">
                        unlike the class
                      </span>
                    )}
                  </th>
                  <td className="py-2 pr-4 text-gray-900 dark:text-white">
                    {typeof s.focus === 'number' ? pct(s.focus) : offLabel(why.eeg)}
                  </td>
                  <td className="py-2 pr-4 text-gray-900 dark:text-white">
                    {typeof s.stress === 'number' ? pct(s.stress) : offLabel(why.eeg)}
                  </td>
                  <td className="py-2 pr-4 text-gray-900 dark:text-white">
                    {typeof s.heart_rate_bpm === 'number'
                      ? `${Math.round(s.heart_rate_bpm)} bpm`
                      : offLabel(why.heart)}
                  </td>
                  <td className="py-2 text-gray-900 dark:text-white">{s.days_recorded ?? 0}</td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </Panel>
  )
}
