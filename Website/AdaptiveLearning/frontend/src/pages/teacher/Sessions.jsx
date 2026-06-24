import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { motion } from 'framer-motion'
import { History, Activity, CheckCircle2, ChevronRight } from 'lucide-react'
import { apiFetch } from '../../lib/api'

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

  useEffect(() => {
    apiFetch('/api/classes').then(rows => {
      setClasses(rows || [])
      if (rows?.length) setClassId(rows[0].id)
      else setLoading(false)
    }).catch(() => setLoading(false))
  }, [])

  useEffect(() => {
    if (!classId) return
    setLoading(true)
    apiFetch(`/api/classes/${classId}/students`).then(async (kids) => {
      setStudents(kids || [])
      const map = {}
      await Promise.all((kids || []).map(async (k) => {
        try {
          map[k.user_id] = await apiFetch(`/api/sessions/student/${k.user_id}`)
        } catch { map[k.user_id] = [] }
      }))
      setSessionsByStudent(map)
      setLoading(false)
    }).catch(() => setLoading(false))
  }, [classId])

  const allRows = students.flatMap(s =>
    (sessionsByStudent[s.user_id] || []).map(sess => ({ ...sess, _student: s }))
  ).sort((a, b) => new Date(b.started_at) - new Date(a.started_at))

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
          <select value={classId} onChange={e => setClassId(e.target.value)}
            className="px-4 py-2.5 rounded-xl border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900 text-sm dark:text-white">
            {classes.map(c => <option key={c.id} value={c.id}>{c.name}</option>)}
          </select>
        )}
      </div>

      {loading ? (
        <div className="space-y-2">{[1,2,3,4].map(i => <div key={i} className="h-16 bg-white dark:bg-gray-900 rounded-2xl border border-gray-100 dark:border-gray-800 animate-pulse" />)}</div>
      ) : allRows.length === 0 ? (
        <div className="text-center py-16 bg-white dark:bg-gray-900 rounded-2xl border border-gray-100 dark:border-gray-800">
          <div className="text-6xl mb-3">📭</div>
          <p className="font-black text-gray-900 dark:text-white">No sessions yet</p>
          <p className="text-sm text-gray-500 mt-1">When students start practicing, sessions will show here.</p>
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
          {allRows.map((s, i) => {
            const live = !s.ended_at
            const acc  = (s.questions_answered || 0) > 0
              ? Math.round(((s.correct_answers || 0) / s.questions_answered) * 100) : null
            return (
              <Link key={s.id} to={`/teacher/sessions/${s.id}`}
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