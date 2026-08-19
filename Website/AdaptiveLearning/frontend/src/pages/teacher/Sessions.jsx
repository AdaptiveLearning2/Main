import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { History, Activity, CheckCircle2, ChevronRight } from 'lucide-react'
import { apiFetch } from '../../lib/api'
import SkeletonList from '../../components/ui/Skeleton'
import LoadError from '../../components/ui/LoadError'

function fmtTime(s) {
  if (!s) return '—'
  const d = new Date(s); return d.toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })
}
function duration(start, end) {
  if (!start) return '—'
  const a = new Date(start).getTime()
  const b = end ? new Date(end).getTime() : Date.now()
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
  // A student whose own session list failed, as opposed to one with no
  // sessions. Counted rather than merged into `failed`: the rest of the class
  // loaded fine and is worth showing.
  const [partial, setPartial] = useState(0)

  // Named, like the seven other pages this change converted. They were left
  // inline here, and the cost was not tidiness: `LoadError` renders its button
  // only when it is handed an `onRetry`, so this was the one converted page
  // whose error state had no way out of it but knowing to reload the browser --
  // which is the thing the whole change says it is fixing.
  const loadClasses = useCallback(() => {
    // No `setLoading(true)` here, unlike the roster read below. It starts true,
    // and on a retry the error stays on screen until the request settles rather
    // than flashing a skeleton -- which is also what keeps this effect off the
    // `set-state-in-effect` lint backlog the project is trying not to grow.
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
      setFailed(true); setLoading(false)
    })
  }, [])

  // No `setLoading(true)` here either, for the reason given above -- and the
  // skeleton a class *switch* needs is raised by the selector below, which is an
  // event handler rather than an effect. That keeps the roster read off the
  // `set-state-in-effect` backlog without letting one class's sessions sit on
  // screen under another class's name while the new ones load.
  const loadSessions = useCallback(() => {
    if (!classId) return
    apiFetch(`/api/classes/${classId}/students`).then(async (kids) => {
      setStudents(kids || [])
      const map = {}
      let missed = 0
      await Promise.all((kids || []).map(async (k) => {
        try {
          map[k.user_id] = await apiFetch(`/api/sessions/student/${k.user_id}`)
        } catch {
          // `[]` here reads downstream as "this student ran no sessions",
          // which is a claim about a child made from a failed request.
          // Recorded as unknown instead, and counted for the note below.
          map[k.user_id] = null
          missed += 1
        }
      }))
      setSessionsByStudent(map)
      setPartial(missed)
      setFailed(false)
      setLoading(false)
    }).catch(e => {
      console.error('Failed to load the class roster:', e)
      setFailed(true); setLoading(false)
    })
  }, [classId])

  useEffect(() => { loadClasses() }, [loadClasses])
  useEffect(() => { loadSessions() }, [loadSessions])

  // Whichever read is the one that failed. `classId` is only set once the class
  // list has arrived, so an empty one means the failure was the outer request.
  const retry = classId ? loadSessions : loadClasses

  const allRows = students.flatMap(s =>
    (sessionsByStudent[s.user_id] || []).map(sess => ({ ...sess, _student: s }))
  ).sort((a, b) => new Date(b.started_at) - new Date(a.started_at))

  // Every student's list failed, which is a *failed load* rather than a partial
  // one -- there is nothing left for it to be partial about.
  //
  // The roster call succeeding while every per-student call fails is not exotic:
  // it is what a degraded `/api/sessions/student/*` looks like for the whole
  // class rather than for one child. `failed` stayed false, `allRows` was empty,
  // and the page drew "N students' sessions couldn't be loaded" directly above
  // "No sessions yet -- when students start practicing, sessions will show
  // here" -- the confident wrong empty state this change exists to remove,
  // reached by the one path it had not covered.
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
        
      {/* Some students' session lists failed while the rest loaded. Said out
          loud, because the table below is missing rows and looks complete. */}
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
        <LoadError what="this class's sessions" onRetry={retry} />
      ) : allRows.length === 0 ? (
        <div className="text-center py-16 bg-white dark:bg-gray-900 rounded-2xl border border-gray-100 dark:border-gray-800">
          <div className="text-6xl mb-3">📭</div>
          <p className="font-black text-gray-900 dark:text-white">No sessions yet</p>
          <p className="text-sm text-gray-500 mt-1">When students start practicing, sessions will show here.</p>
        </div>
      ) : filteredRows.length === 0 ? (
        <div className="text-center py-16 bg-white dark:bg-gray-900 rounded-2xl border border-gray-100 dark:border-gray-800">
          <div className="text-6xl mb-3">🔍</div>
          <p className="font-black text-gray-900 dark:text-white">No sessions match "{search}"</p>
        </div>
      ) : (
        <div className="bg-white dark:bg-gray-900 rounded-2xl border border-gray-100 dark:border-gray-800 shadow-sm overflow-hidden">
          <div className="grid grid-cols-12 px-5 py-3 border-b border-gray-50 dark:border-gray-800 text-[11px] uppercase tracking-wider text-gray-400 font-bold">
            <div className="col-span-3">Student</div>
            <div className="col-span-3">Started</div>
            <div className="col-span-2">Duration</div>
            <div className="col-span-2">Progress</div>
            <div className="col-span-2 text-right">Status</div>
          </div>
          {filteredRows.map((s) => {
            const live = !s.ended_at
            const acc  = (s.questions_answered || 0) > 0
              ? Math.round(((s.correct_answers || 0) / s.questions_answered) * 100) : null
            return (
              <Link key={s.id} to={`/teacher/sessions/${s.id}`}
                // The review's "Back" was hardcoded to Live Monitoring, so
                // opening a session from this history and coming back landed
                // on a different page with the teacher's place in the list
                // gone.
                state={{ from: '/teacher/sessions' }}
                className="grid grid-cols-12 items-center px-5 py-4 border-b border-gray-50 dark:border-gray-800 last:border-0 hover:bg-slate-50 dark:hover:bg-gray-800 transition group">
                <div className="col-span-3 flex items-center gap-3">
                  <div className="w-8 h-8 bg-gradient-to-br from-violet-400 to-purple-500 rounded-full flex items-center justify-center text-white text-xs font-black flex-shrink-0">
                    {(s._student?.name || '?')[0].toUpperCase()}
                  </div>
                  <span className="text-sm font-bold text-gray-900 dark:text-white truncate">{s._student?.name || 'Student'}</span>
                </div>
                <div className="col-span-3 text-sm text-gray-500 dark:text-gray-400">{fmtTime(s.started_at)}</div>
                <div className="col-span-2 text-sm text-gray-500 dark:text-gray-400">{duration(s.started_at, s.ended_at)}</div>
                <div className="col-span-2 text-sm text-gray-700 dark:text-gray-300 flex items-center gap-2">
                  <Activity size={13} className="text-emerald-500" />
                  {s.questions_answered || 0} q
                  {acc !== null && <span className="text-xs text-gray-400">· {acc}%</span>}
                </div>
                <div className="col-span-2 flex items-center justify-end gap-2">
                  {live
                    ? <span className="text-[10px] font-bold px-2 py-1 bg-emerald-100 dark:bg-emerald-900/40 text-emerald-700 dark:text-emerald-300 rounded-full animate-pulse">● LIVE</span>
                    : <span className="text-[10px] font-bold px-2 py-1 bg-gray-100 dark:bg-gray-800 text-gray-500 rounded-full flex items-center gap-1"><CheckCircle2 size={10} /> done</span>}
                  <ChevronRight size={14} className="text-gray-300 group-hover:text-violet-500 transition" />
                </div>
              </Link>
            )
          })}
        </div>
      )}
    </div>
  )
}