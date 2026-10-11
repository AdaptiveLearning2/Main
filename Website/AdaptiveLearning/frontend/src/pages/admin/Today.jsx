import useAdminRead from '../../hooks/useAdminRead'
import { ReadState, Tile, Unread } from './adminUi'
import { reasonLabel } from './closeReasons'

const TODAY = '/api/admin/today'
const POLL_MS = 60_000

function Counts({ data }) {
  if (!data.retrieved) return <Unread what="Today's session counts" />
  const c = data.counts
  const practiceAnswers = data.practice_answers
  return (
    <div className="space-y-2">
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        <Tile label="Lessons started" value={c.started} />
        <Tile label="Open now" value={c.open_now} hint="any day, not ended yet" />
        <Tile label="Answers" value={c.answers + (practiceAnswers ?? 0)}
              hint={practiceAnswers == null ? 'adaptive only: practice could not be read'
                : `${c.answers} adaptive, ${practiceAnswers} practice`} />
        <Tile label="Students active" value={c.active_students} />
      </div>
      <p className="text-xs text-gray-600 dark:text-gray-400">
        Since local midnight ({data.timezone}). Teacher and parent activity is not recorded, so it is not shown.
      </p>
    </div>
  )
}

function Ends({ data }) {
  const ended = data.retrieved ? Object.entries(data.counts.ended_by_reason || {}) : null
  const discarded = Object.entries(data.discarded || {})
  return (
    <div className="space-y-2">
      {ended === null
        ? <p className="text-sm text-amber-800 dark:text-amber-300">How today&rsquo;s lessons ended could not be read.</p>
        : ended.length === 0
          ? <p className="text-sm text-gray-600 dark:text-gray-400">No lesson has ended today.</p>
          : (
            <ul className="text-sm space-y-1">
              {ended.sort((a, b) => b[1] - a[1]).map(([reason, n]) => (
                <li key={reason} className="text-gray-900 dark:text-white">
                  <span className="tabular-nums font-bold">{n}</span> × {reasonLabel(reason)}
                </li>
              ))}
            </ul>
          )}
      {!data.discarded_retrieved
        ? <p className="text-xs text-amber-800 dark:text-amber-300">Discarded empty lessons could not be counted.</p>
        : discarded.length > 0 && (
          <p className="text-xs text-gray-600 dark:text-gray-400">
            Also discarded, having recorded nothing:{' '}
            {discarded.map(([reason, n]) => `${n} ${reasonLabel(reason).toLowerCase()}`).join('; ')}.
          </p>
        )}
    </div>
  )
}

/** Overview's Today strip and how today's lessons ended, from one read. */
export default function Today() {
  const res = useAdminRead(TODAY, POLL_MS)
  return (
    <ReadState res={res} what="today's counts">
      {data => (
        <div className="space-y-4">
          <Counts data={data} />
          <div className="space-y-2">
            <h3 className="text-xs font-black uppercase tracking-wide text-gray-600 dark:text-gray-400">
              How lessons ended today
            </h3>
            <Ends data={data} />
          </div>
        </div>
      )}
    </ReadState>
  )
}
