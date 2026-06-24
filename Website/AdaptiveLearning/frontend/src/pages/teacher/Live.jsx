import { useEffect, useRef, useState } from 'react'
import { motion } from 'framer-motion'
import { Activity, Camera, Brain, Radio } from 'lucide-react'
import { Link } from 'react-router-dom'
import { LineChart, Line, ResponsiveContainer, YAxis } from 'recharts'
import { apiFetch } from '../../lib/api'

const EMOJI = {
  happy: '😀', neutral: '😐', confused: '😕', frustrated: '😤',
  sad: '😢', surprised: '😮', angry: '😠'
}

function Gauge({ label, value, color = 'bg-violet-500' }) {
  const pct = value == null ? 0 : Math.round(Math.max(0, Math.min(1, value)) * 100)
  return (
    <div>
      <div className="flex justify-between mb-1">
        <span className="text-[11px] uppercase tracking-wider text-gray-500 font-bold">{label}</span>
        <span className="text-[11px] font-black text-gray-800 dark:text-gray-200">
          {value == null ? '—' : `${pct}%`}
        </span>
      </div>
      <div className="h-2 bg-gray-100 dark:bg-gray-800 rounded-full overflow-hidden">
        <motion.div
          className={`h-full ${color} rounded-full`}
          animate={{ width: `${pct}%` }}
          transition={{ duration: 0.4 }}
        />
      </div>
    </div>
  )
}

function StudentCard({ student, history }) {
  const active = student.active_session
  const cog    = student.latest_cognitive
  const face   = student.latest_face
  const initial = (student.name || '?')[0].toUpperCase()

  return (
    <motion.div
      layout
      className="bg-white dark:bg-gray-900 rounded-2xl border border-gray-100 dark:border-gray-800 shadow-sm p-5"
    >
      <div className="flex items-center gap-3 mb-4">
        <div className="w-10 h-10 bg-gradient-to-br from-violet-400 to-purple-500 rounded-full flex items-center justify-center text-white font-black">
          {initial}
        </div>
        <div className="flex-1 min-w-0">
          <p className="text-sm font-black text-gray-900 dark:text-white truncate">{student.name}</p>
          <p className="text-[11px] text-gray-400 truncate">{student.email}</p>
        </div>
        <span className={`text-[10px] font-bold px-2 py-1 rounded-full ${active ? 'bg-emerald-100 text-emerald-700 dark:bg-emerald-900/40 dark:text-emerald-300' : 'bg-gray-100 text-gray-500 dark:bg-gray-800 dark:text-gray-400'}`}>
          {active ? '● LIVE' : 'idle'}
        </span>
      </div>

      <div className="flex gap-2 mb-4 text-[10px]">
        <span className={`px-2 py-1 rounded-full font-bold flex items-center gap-1 ${cog ? 'bg-indigo-100 text-indigo-700 dark:bg-indigo-900/40 dark:text-indigo-300' : 'bg-gray-100 text-gray-400 dark:bg-gray-800'}`}>
          <Brain size={11} /> Headband {cog ? 'on' : 'off'}
        </span>
        <span className={`px-2 py-1 rounded-full font-bold flex items-center gap-1 ${face ? 'bg-pink-100 text-pink-700 dark:bg-pink-900/40 dark:text-pink-300' : 'bg-gray-100 text-gray-400 dark:bg-gray-800'}`}>
          <Camera size={11} /> Camera {face ? 'on' : 'off'}
        </span>
      </div>

      <div className="space-y-3 mb-4">
        <Gauge label="Focus"      value={cog?.focus}      color="bg-indigo-500" />
        <Gauge label="Engagement" value={cog?.engagement} color="bg-emerald-500" />
        <Gauge label="Stress"     value={cog?.stress}     color="bg-rose-500" />
        <Gauge label="Attention"  value={face?.attention} color="bg-amber-500" />
      </div>

      {face?.emotion && (
        <div className="flex items-center gap-2 mb-4 text-sm">
          <span className="text-2xl">{EMOJI[face.emotion] || '🙂'}</span>
          <span className="capitalize font-bold text-gray-700 dark:text-gray-300">{face.emotion}</span>
        </div>
      )}

      <div className="h-12 -mx-1">
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={history}>
            <YAxis hide domain={[0, 1]} />
            <Line type="monotone" dataKey="focus"      stroke="#6366f1" strokeWidth={1.5} dot={false} isAnimationActive={false} />
            <Line type="monotone" dataKey="engagement" stroke="#10b981" strokeWidth={1.5} dot={false} isAnimationActive={false} />
            <Line type="monotone" dataKey="stress"     stroke="#f43f5e" strokeWidth={1.5} dot={false} isAnimationActive={false} />
          </LineChart>
        </ResponsiveContainer>
      </div>

      {active && (
        <Link
          to={`/teacher/sessions/${active.id}`}
          className="mt-4 block text-center text-xs font-bold text-violet-600 hover:text-violet-700 dark:text-violet-400"
        >
          Open full session →
        </Link>
      )}
    </motion.div>
  )
}

export default function Live() {
  const [classes, setClasses]     = useState([])
  const [classId, setClassId]     = useState('')
  const [students, setStudents]   = useState([])
  const [error, setError]         = useState(null)
  const historyRef = useRef({}) // user_id -> [{focus, engagement, stress}]

  useEffect(() => {
    apiFetch('/api/classes')
      .then(rows => {
        setClasses(rows)
        if (rows.length && !classId) setClassId(rows[0].id)
      })
      .catch(e => setError(e.message))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    if (!classId) return
    let killed = false
    const tick = async () => {
      try {
        const rows = await apiFetch(`/api/teacher/classes/${classId}/live`)
        if (killed) return
        // append to per-student history (last 60 points)
        rows.forEach(r => {
          const c = r.latest_cognitive
          if (!c) return
          const arr = historyRef.current[r.user_id] || []
          arr.push({ focus: c.focus ?? 0, engagement: c.engagement ?? 0, stress: c.stress ?? 0 })
          while (arr.length > 60) arr.shift()
          historyRef.current[r.user_id] = arr
        })
        setStudents(rows)
      } catch (e) {
        if (!killed) setError(e.message)
      }
    }
    tick()
    const id = setInterval(tick, 1000)
    return () => { killed = true; clearInterval(id) }
  }, [classId])

  return (
    <div className="p-6 lg:p-8 pb-12">
      <div className="mb-6 flex items-end justify-between flex-wrap gap-4">
        <div>
          <h1 className="text-3xl font-black text-gray-900 dark:text-white flex items-center gap-3">
            <Radio className="text-violet-600" size={28} />
            Live Monitoring
          </h1>
          <p className="text-gray-500 dark:text-gray-400 mt-1 text-sm flex items-center gap-2">
            <Activity size={14} className="text-emerald-500 animate-pulse" />
            Real-time focus, stress, engagement and emotion across your class.
          </p>
        </div>

        {classes.length > 0 && (
          <select
            value={classId}
            onChange={e => setClassId(e.target.value)}
            className="px-4 py-2.5 rounded-xl border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900 text-sm dark:text-white"
          >
            {classes.map(c => <option key={c.id} value={c.id}>{c.name}</option>)}
          </select>
        )}
      </div>

      {error && <p className="text-sm text-rose-500 mb-4">⚠️ {error}</p>}

      {!classes.length ? (
        <div className="text-center py-16">
          <div className="text-6xl mb-3">🏫</div>
          <p className="font-black text-gray-900 dark:text-white">No classes yet</p>
          <p className="text-sm text-gray-500 mt-1">Create a class first under the Classes tab.</p>
        </div>
      ) : students.length === 0 ? (
        <div className="text-center py-16">
          <div className="text-6xl mb-3">👀</div>
          <p className="font-black text-gray-900 dark:text-white">Nobody's joined yet</p>
          <p className="text-sm text-gray-500 mt-1">Share the join code so students can hop in.</p>
        </div>
      ) : (
        <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-3 gap-5">
          {students.map(s => (
            <StudentCard
              key={s.user_id}
              student={s}
              history={historyRef.current[s.user_id] || []}
            />
          ))}
        </div>
      )}
    </div>
  )
}