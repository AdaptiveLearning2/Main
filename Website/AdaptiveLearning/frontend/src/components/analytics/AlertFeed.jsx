import { AlertTriangle, WifiOff, Clock } from 'lucide-react'
import Panel from './Panel'

/**
 * Operational alerts for a class, newest first.
 *
 * **What this deliberately never shows.** Every row is a checkable fact about
 * a session — it timed out, or recording was expected and nothing arrived.
 * None of them is a claim about the student. `signal_fusion` produces a
 * "stressed" label and it is not routed here, for the reason the `attention`
 * tiles were removed (#86): a timestamped event reads as objective, and that
 * inference is not validated on this product's users.
 *
 * There is no dismiss button, and that is a decision rather than an omission.
 * Both kinds are about a session that has already ended — a lesson that timed
 * out yesterday stays timed out — so there is nothing to resolve. A dismissal
 * implies a triage workflow this product does not have, and the seven-day
 * window already bounds what is on screen.
 */

/** One entry per whitelisted `kind`, and the map is the whitelist.
 *
 * An unknown kind renders as a plain row with its raw name rather than being
 * dropped: the database CHECK means it should be impossible, and if it does
 * happen a visible unstyled row is what gets it reported. Silently skipping it
 * would make a real alert invisible.
 */
const KINDS = {
  session_auto_closed: {
    Icon: Clock,
    title: 'Session timed out',
    tone: 'text-amber-700 dark:text-amber-300 bg-amber-50 dark:bg-amber-900/30',
    describe: d => {
      const n = d?.questions_answered
      return typeof n === 'number'
        ? `Ended without being finished, after ${n} ${n === 1 ? 'question' : 'questions'}. The work was saved.`
        : 'Ended without being finished. The work was saved.'
    },
  },
  signals_missing: {
    Icon: WifiOff,
    title: 'No headband data',
    tone: 'text-rose-700 dark:text-rose-300 bg-rose-50 dark:bg-rose-900/30',
    describe: () =>
      'Recording was switched on for this student, but no readings arrived. '
      + 'Check the headband is paired and the local service is running.',
  },
}

const FALLBACK = {
  Icon: AlertTriangle,
  title: 'Unrecognised alert',
  tone: 'text-gray-700 dark:text-gray-300 bg-gray-100 dark:bg-gray-800',
  describe: () => 'This version of the app does not know how to describe it.',
}

/** "09:30" in the reader's locale — the school day is already on the row. */
function timeOf(iso) {
  const d = new Date(iso)
  return Number.isNaN(d.getTime())
    ? null : d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
}

export default function AlertFeed({ data, loading, onRetry }) {
  const alerts = data?.alerts || []

  return (
    <Panel
      title="Needs a look"
      note={`Things that went wrong with a lesson in the last ${data?.days || 7} days. Never a judgement about a student.`}
      loading={loading}
      failed={data?.retrieved === false}
      what="the alert feed"
      onRetry={onRetry}
      empty={!alerts.length}
      emptyNote="Nothing to flag. Sessions are finishing normally and readings are arriving."
      className="lg:col-span-2"
    >
      <ul className="space-y-2">
        {alerts.map(a => {
          const { Icon, title, tone, describe } = KINDS[a.kind] || FALLBACK
          const at = timeOf(a.created_at)
          return (
            <li key={a.id}
              className="flex items-start gap-3 rounded-xl border border-gray-100 dark:border-gray-800 p-3">
              <span className={`shrink-0 rounded-lg p-2 ${tone}`}>
                <Icon size={16} aria-hidden="true" />
              </span>
              <div className="min-w-0">
                <p className="text-sm font-bold text-gray-900 dark:text-white">
                  {a.student_name} — {KINDS[a.kind] ? title : `${title} (${a.kind})`}
                </p>
                <p className="text-xs text-gray-600 dark:text-gray-400">
                  {describe(a.detail)}
                </p>
                {/* The school day, from the payload, not re-derived here: a
                    teacher marking from another timezone should still see the
                    day the lesson was taught on. */}
                <p className="mt-0.5 text-xs text-gray-600 dark:text-gray-400">
                  <time dateTime={a.created_at}>
                    {a.school_day}{at && ` at ${at}`}
                  </time>
                </p>
              </div>
            </li>
          )
        })}
      </ul>
      {data?.truncated && (
        <p className="mt-3 text-xs text-gray-600 dark:text-gray-400">
          Showing the most recent {alerts.length}. There are more in this window.
        </p>
      )}
    </Panel>
  )
}
