import { useEffect, useRef, useState } from 'react'
import { motion } from 'framer-motion'
import { Activity, Camera, Brain, Heart, Radio } from 'lucide-react'
import { Link } from 'react-router-dom'
import { LineChart, Line, ResponsiveContainer, YAxis } from 'recharts'
import { apiFetch } from '../../lib/api'

// Spelled out rather than shown raw. `muse_optics` means nothing to a teacher,
// and the distinction that matters to them is headband versus camera.
const SOURCE_LABEL = {
  muse_optics: 'Heart · headband',
  muse_ppg:    'Heart · headband',
  rppg:        'Heart · camera',
}

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
  const heart  = student.latest_heart
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
        {/* Named, not just present. Accuracy differs materially by sensor, and
            a session can fail over mid-way -- a teacher watching the trace
            change shape needs to be able to see the sensor changed rather than
            read it as the student changing. `trusted: false` is shown as
            "weak signal" rather than hidden: a card that goes blank while a
            sensor is still producing readings is indistinguishable from one
            that stopped. */}
        {heart && (
          <span className={`px-2 py-1 rounded-full font-bold flex items-center gap-1 ${
            heart.trusted === false
              ? 'bg-gray-100 text-gray-400 dark:bg-gray-800'
              : 'bg-purple-100 text-purple-700 dark:bg-purple-900/40 dark:text-purple-300'
          }`}>
            <Heart size={11} /> {SOURCE_LABEL[heart.source] || heart.source}
            {heart.trusted === false && ' · weak signal'}
          </span>
        )}
      </div>

      <div className="space-y-3 mb-4">
        <Gauge label="Focus"      value={cog?.focus}      color="bg-indigo-500" />
        <Gauge label="Engagement" value={cog?.engagement} color="bg-emerald-500" />
        <Gauge label="Stress"     value={cog?.stress}     color="bg-rose-500" />
        <Gauge label="Attention"  value={face?.attention} color="bg-amber-500" />
      </div>

      {/* Absolute units, so it is stated rather than drawn on the 0..1
          gauges above -- a bpm on a ratio scale is either invisible at the
          floor or a lie about the axis. Null bpm on a present row means the
          window was rejected, which is not a reading of zero. */}
      {typeof heart?.heart_rate_bpm === 'number' && (
        <div className="flex items-center gap-2 mb-4 text-sm">
          <Heart size={16} className="text-purple-500" />
          <span className="font-bold text-gray-700 dark:text-gray-300">
            {Math.round(heart.heart_rate_bpm)} bpm
          </span>
          {typeof heart.rmssd_ms === 'number' && (
            <span className="text-xs text-gray-400">HRV {Math.round(heart.rmssd_ms)} ms</span>
          )}
        </div>
      )}

      {face?.emotion && (
        <div className="flex items-center gap-2 mb-4 text-sm">
          <span className="text-2xl">{EMOJI[face.emotion] || '🙂'}</span>
          <span className="capitalize font-bold text-gray-700 dark:text-gray-300">{face.emotion}</span>
        </div>
      )}

      <div className="h-12 -mx-1">
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={history}>
            {/* Two hidden axes with explicit ids. Heart rate is bpm and the
                others are ratios; sharing one axis would flatten the ratios
                against the floor. No pie or legend here -- the card is small,
                and a pie of a 60-point rolling window misleads. */}
            <YAxis yAxisId="ratio" hide domain={[0, 1]} />
            <YAxis yAxisId="bpm" hide domain={['auto', 'auto']} />
            <Line yAxisId="ratio" type="monotone" dataKey="focus"      stroke="#6366f1" strokeWidth={1.5} dot={false} isAnimationActive={false} />
            <Line yAxisId="ratio" type="monotone" dataKey="engagement" stroke="#10b981" strokeWidth={1.5} dot={false} isAnimationActive={false} />
            <Line yAxisId="ratio" type="monotone" dataKey="stress"     stroke="#f43f5e" strokeWidth={1.5} dot={false} isAnimationActive={false} />
            <Line yAxisId="bpm"   type="monotone" dataKey="bpm"        stroke="#a855f7" strokeWidth={1.5} dot={false} connectNulls isAnimationActive={false} />
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
        // Reads facial signals (the attention gauge, the current emotion and
        // the camera badge below) and deliberately does NOT honour the
        // "Hide sensor data" switch in lib/viewPrefs.js -- that control covers
        // the reporting surfaces, which render it, and this page does not.
        // See the scope note there before wiring it in: doing so also means
        // putting the switch on this page, because a control that silently
        // changes a page it is absent from is worse than one with a stated
        // edge.
        const rows = await apiFetch(`/api/teacher/classes/${classId}/live`)
        if (killed) return
        // append to per-student history (last 60 points)
        rows.forEach(r => {
          const c = r.latest_cognitive
          const h = r.latest_heart
          // Not `if (!c) return`: a student can consent to the heart sensor
          // without EEG (independent switches), in which case c is always
          // null while h keeps arriving. Skipping the whole tick on c alone
          // left that student's badge showing a live bpm with a permanently
          // empty trend line beneath it.
          if (!c && !h) return
          // Copied, never mutated in place. This array is handed to the chart
          // as a prop on line 269, and once it has been rendered something
          // downstream makes it non-extensible -- so the next tick's `push`
          // threw `Cannot add property 1, object is not extensible`, which the
          // catch below turned into a banner across the whole page.
          //
          // It needed two ticks of data for one student to appear, so it stayed
          // hidden until a session was actually streaming. Rebuilding rather
          // than chasing who freezes it: a published array should not be edited
          // afterwards regardless, and this removes the question.
          const arr = historyRef.current[r.user_id] || []
          // Null, not 0. A row can exist with null measurements when the
          // headband reported bad electrode contact -- the row is kept so the
          // session's timeline stays intact, but there is no measurement.
          // Coercing that to 0 draws it as "totally unfocused, perfectly calm",
          // which is a fabricated reading presented as a real one. recharts
          // leaves a gap for null (connectNulls defaults to false).
          const point = {
            focus:      c?.focus ?? null,
            engagement: c?.engagement ?? null,
            stress:     c?.stress ?? null,
            // Null when there is no heart row for this tick, or when the row
            // exists with a rejected window. Same reasoning as the three above:
            // recharts leaves a gap for null, and a 0 here would draw a
            // flatlined heart rate that never happened.
            bpm:        typeof h?.heart_rate_bpm === 'number' ? h.heart_rate_bpm : null,
          }
          // `slice(-60)` rather than a shift loop, for the same reason: it
          // returns a new array instead of editing the one already on screen.
          historyRef.current[r.user_id] = [...arr, point].slice(-60)
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