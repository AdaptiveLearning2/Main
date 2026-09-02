import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { History, Activity, CheckCircle2, ChevronRight, AlertTriangle } from 'lucide-react'
import { apiFetch } from '../../lib/api'
import SkeletonList from '../../components/ui/Skeleton'
import LoadError from '../../components/ui/LoadError'
import { useLatestRequest } from '../../hooks/useLatestRequest'

function fmtTime(s) {
  if (!s) return '—'
  const d = new Date(s); return d.toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })
}
/** How long a session ran, or how long it has been open.
 *
 * An abandoned session gets no duration at all. Counting to `Date.now()` for
 * one nobody has touched since June produced "83132m 45s", which is not a
 * measurement of anything — the student left within the hour and the clock
 * kept running. A dash says the honest thing: we do not know when it ended.
 *
 * An **idle** session is the same error at a smaller scale, and it was left
 * behind when `idle` was added as a fourth badge: the badge stopped claiming
 * the session was live while this cell went on ticking to now for up to the
 * six hours before `abandoned` takes over. Here we are not guessing, though —
 * `last_activity_at` is the newest answer, which is when the student actually
 * stopped — so it measures to that rather than dashing. A dash would throw
 * away a figure the backend already sends.
 *
 * Falling back to a dash when that timestamp is missing matters for the same
 * reason `activity_known` gates the badge: an unknown last activity must not
 * become a measurement, and it must not silently resume ticking either.
 */
function duration(start, end, { abandoned = false, idle = false, lastActivity = null } = {}) {
  if (!start) return '—'
  if (!end && abandoned) return '—'
  if (!end && idle && !lastActivity) return '—'
  const a = new Date(start).getTime()
  const b = end ? new Date(end).getTime()
    : idle ? new Date(lastActivity).getTime()
    : Date.now()
  const sec = Math.max(0, Math.round((b - a) / 1000))
  const m = Math.floor(sec / 60); const s = sec % 60
  return `${m}m ${s}s`
}

export default function Sessions() {
  const [classes, setClasses] = useState([])
  const [classId, setClassId] = useState('')
  const [students, setStudents] = useState([])
  const [sessionsByStudent, setSessionsByStudent] = useState({})
  const [loading, setLoading] = useState(true)
  const [search, setSearch] = useState('')

  const [failed, setFailed] = useState(false)
  // Count of students whose own session list failed to load, kept separate
  // from `failed` since the rest of the class still loaded fine.
  const [partial, setPartial] = useState(0)

  const loadClasses = useCallback(() => {
    // No setLoading(true) here: loading already starts true, and on retry
    // the error stays on screen instead of flashing a skeleton.
    apiFetch('/api/classes').then(rows => {
      setClasses(rows || [])
      setFailed(false)
      if (rows?.length) {
        // Raised here, not only by the selector. Picking a class programmatically
        // is the other way `loadSessions` gets triggered, and on a *retry*
        // `loading` is already false -- so between this line and that response
        // the page had no rows, no error and no skeleton, and drew "No sessions
        // yet" over a class whose roster was still on its way. The first load
        // never showed it, because `loading` starts true; only the retry path
        // did, which is why it survived the tests.
        setLoading(true)
        setClassId(rows[0].id)
      } else {
        setLoading(false)
      }
    }).catch(e => {
      console.error('Failed to load classes:', e)
      setFailed(e); setLoading(false)
    })
  }, [])

  // No setLoading(true) here either -- the class selector's onChange raises
  // the skeleton on switch, so one class's sessions don't linger under another's name.

  // Tracks which roster read is the current one. This fetch fans out per
  // student and is the slowest on the page, so a class switch mid-flight could
  // let a stale response land last and repaint the list under the new class's
  // name. Shared with StudentProgressReport's strategy request -- see
  // hooks/useLatestRequest.
  const beginRosterRead = useLatestRequest()

  const loadSessions = useCallback(() => {
    if (!classId) return
    const current = beginRosterRead()
    apiFetch(`/api/classes/${classId}/students`).then(async (kids) => {
      if (!current()) return
      setStudents(kids || [])
      const map = {}
      let missed = 0
      await Promise.all((kids || []).map(async (k) => {
        try {
          map[k.user_id] = await apiFetch(`/api/sessions/student/${k.user_id}`)
        } catch {
          // null, not []: an empty array would read as "ran no sessions", which is untrue for a failed request.
          map[k.user_id] = null
          missed += 1
        }
      }))
      // Checked again after the fan-out, not only before it: the per-student
      // reads are where the time goes, so this is the window a class switch
      // actually lands in.
      if (!current()) return
      setSessionsByStudent(map)
      setPartial(missed)
      setFailed(false)
      setLoading(false)
    }).catch(e => {
      if (!current()) return
      console.error('Failed to load the class roster:', e)
      setFailed(e); setLoading(false)
    })
  }, [classId, beginRosterRead])

  useEffect(() => { loadClasses() }, [loadClasses])
  useEffect(() => { loadSessions() }, [loadSessions])

  // classId is only set once the class list has arrived, so an empty one means loadClasses failed.
  const retry = classId ? loadSessions : loadClasses

  const allRows = students.flatMap(s =>
    (sessionsByStudent[s.user_id] || []).map(sess => ({ ...sess, _student: s }))
  ).sort((a, b) => new Date(b.started_at) - new Date(a.started_at))

  // If every student's list failed, treat it as a full failure rather than a partial one.
  const allFailed = students.length > 0 && partial === students.length

  const filteredRows = allRows.filter(s =>
    (s._student?.name || '').toLowerCase().includes(search.trim().toLowerCase())
  )

  return (
    <div className="p-6 lg:p-8 pb-12">
      <div className="mb-6 flex items-end justify-between flex-wrap gap-4">
        <div>
          <h1 className="text-3xl font-black text-gray-900 dark:text-white flex items-center gap-3">
            <History className="text-violet-600" size={28} /> Sessions
          </h1>
          <p className="text-gray-500 dark:text-gray-400 mt-1 text-sm">All past and current learning sessions in this class. Click into any to see cognitive replay.</p>
        </div>
        {classes.length > 0 && (
          <div className="flex gap-3">
            <input
              type="text"
              value={search}
              onChange={e => setSearch(e.target.value)}
              placeholder="Filter by student name..."
              className="px-4 py-2.5 rounded-xl border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900 text-sm dark:text-white w-48"
            />
            <select value={classId} onChange={e => { setClassId(e.target.value); setLoading(true) }}
              className="px-4 py-2.5 rounded-xl border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900 text-sm dark:text-white">
              {classes.map(c => <option key={c.id} value={c.id}>{c.name}</option>)}
            </select>
          </div>
        )}
      </div>
        
      {/* Says so explicitly, since the table below would otherwise look complete with rows missing. */}
      {!loading && !failed && !allFailed && partial > 0 && (
        <div className="mb-4 rounded-2xl border border-amber-200 dark:border-amber-800 bg-amber-50 dark:bg-amber-950/40 px-5 py-3">
          <p className="text-sm font-bold text-amber-900 dark:text-amber-200">
            {partial} student{partial === 1 ? "'s" : "s'"} sessions couldn&apos;t be
            loaded, so this list is incomplete.
          </p>
        </div>
      )}

      {loading ? (
        <SkeletonList count={4} height="h-16" gap="space-y-2" />
      ) : failed || allFailed ? (
        // `failed` holds the error, `allFailed` is derived from per-student
        // reads that each failed separately -- there is no one error to blame
        // for that, so it falls through to the generic sentence.
        <LoadError what="this class's sessions" onRetry={retry}
          error={failed || undefined} />
      ) : allRows.length === 0 ? (
        <div className="text-center py-16 bg-white dark:bg-gray-900 rounded-2xl border border-gray-100 dark:border-gray-800">
          <div className="text-6xl mb-3">📭</div>
          <p className="font-black text-gray-900 dark:text-white">No sessions yet</p>
          <p className="text-sm text-gray-500 mt-1 dark:text-gray-400">When students start practicing, sessions will show here.</p>
        </div>
      ) : filteredRows.length === 0 ? (
        <div className="text-center py-16 bg-white dark:bg-gray-900 rounded-2xl border border-gray-100 dark:border-gray-800">
          <div className="text-6xl mb-3">🔍</div>
          <p className="font-black text-gray-900 dark:text-white">No sessions match "{search}"</p>
        </div>
      ) : (
        <div className="bg-white dark:bg-gray-900 rounded-2xl border border-gray-100 dark:border-gray-800 shadow-sm overflow-hidden">
          <div className="grid grid-cols-12 px-5 py-3 border-b border-gray-50 dark:border-gray-800 text-[11px] uppercase tracking-wider text-gray-600 font-bold dark:text-gray-400">
            <div className="col-span-3">Student</div>
            <div className="col-span-3">Started</div>
            <div className="col-span-2">Duration</div>
            <div className="col-span-2">Progress</div>
            <div className="col-span-2 text-right">Status</div>
          </div>
          {filteredRows.map((s) => {
            // Three states, not two. `!ended_at` alone was claiming LIVE for
            // sessions open since June, complete with a pulsing dot. The
            // backend decides which are abandoned so the threshold has one
            // definition -- see `student_sessions`.
            const abandoned = !s.ended_at && s.abandoned === true
            // Four states now. `abandoned` is an *age* (6h) and only stops the
            // list claiming a session from June is in progress; `idle` is real
            // quiet, computed by the backend from the newest answer against
            // the same window `class_live` uses. Without it a student who
            // answered three questions and closed the laptop read LIVE, with
            // the duration ticking up, for six hours.
            //
            // `=== true` on both, and `activity_known` gates idle: a failed
            // read of last activity must not relabel a live session as quiet,
            // which is the same error as reporting a failed count as a quiet
            // week.
            const idle = !s.ended_at && !abandoned && s.idle === true
            const live = !s.ended_at && !abandoned && !idle
            const acc  = (s.questions_answered || 0) > 0
              ? Math.round(((s.correct_answers || 0) / s.questions_answered) * 100) : null
            return (
              <Link key={s.id} to={`/teacher/sessions/${s.id}`}
                // So the session review's "Back" returns here instead of Live Monitoring.
                state={{ from: '/teacher/sessions' }}
                className="grid grid-cols-12 items-center px-5 py-4 border-b border-gray-50 dark:border-gray-800 last:border-0 hover:bg-slate-50 dark:hover:bg-gray-800 transition group">
                <div className="col-span-3 flex items-center gap-3">
                  <div className="w-8 h-8 bg-gradient-to-br from-violet-400 to-purple-500 rounded-full flex items-center justify-center text-white text-xs font-black flex-shrink-0">
                    {(s._student?.name || '?')[0].toUpperCase()}
                  </div>
                  <span className="text-sm font-bold text-gray-900 dark:text-white truncate">{s._student?.name || 'Student'}</span>
                </div>
                <div className="col-span-3 text-sm text-gray-500 dark:text-gray-400">{fmtTime(s.started_at)}</div>
                <div className="col-span-2 text-sm text-gray-500 dark:text-gray-400">{duration(s.started_at, s.ended_at, { abandoned, idle, lastActivity: s.last_activity_at })}</div>
                <div className="col-span-2 text-sm text-gray-700 dark:text-gray-300 flex items-center gap-2">
                  <Activity size={13} className="text-emerald-500" />
                  {s.questions_answered || 0} q
                  {acc !== null && <span className="text-xs text-gray-600 dark:text-gray-400">· {acc}%</span>}
                </div>
                <div className="col-span-2 flex items-center justify-end gap-2">
                  {live && <span className="text-[10px] font-bold px-2 py-1 bg-emerald-100 dark:bg-emerald-900/40 text-emerald-700 dark:text-emerald-300 rounded-full animate-pulse">● LIVE</span>}
                  {abandoned && (
                    // Not an error, and not "done" either: it was never ended,
                    // so its questions were never credited. The nightly sweep
                    // closes these; until it has, saying so is more use than
                    // either of the other two badges.
                    <span title="Never ended — the student left without finishing. The nightly sweep will close it."
                      className="text-[10px] font-bold px-2 py-1 bg-amber-100 dark:bg-amber-900/40 text-amber-800 dark:text-amber-300 rounded-full flex items-center gap-1">
                      <AlertTriangle size={10} /> never ended
                    </span>
                  )}
                  {idle && (
                    <span className="text-[10px] font-bold px-2 py-1 bg-amber-100 dark:bg-amber-900/40 text-amber-700 dark:text-amber-300 rounded-full">
                      idle
                    </span>
                  )}
                  {!live && !abandoned && !idle && <span className="text-[10px] font-bold px-2 py-1 bg-gray-100 dark:bg-gray-800 text-gray-600 dark:text-gray-300 rounded-full flex items-center gap-1"><CheckCircle2 size={10} /> done</span>}
                  <ChevronRight size={14} className="text-gray-600 group-hover:text-violet-500 transition dark:text-gray-400" />
                </div>
              </Link>
            )
          })}
        </div>
      )}
    </div>
  )
}