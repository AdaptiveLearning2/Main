import { useEffect, useRef, useState } from 'react'
import { motion } from 'framer-motion'
import { Activity, Camera, Brain, Heart, Radio } from 'lucide-react'
import { Link } from 'react-router-dom'
import { LineChart, Line, YAxis } from 'recharts'
import AccessibleChart from '../../components/charts/AccessibleChart'
import { asPercent } from '../../components/charts/describeSeries'
import { apiFetch } from '../../lib/api'
import { STALE_AFTER_S, eegWeak, formatAge } from '../../lib/signalAge'
import SkeletonList from '../../components/ui/Skeleton'

// POLL_MAX_MS caps the backoff when the endpoint is failing, so a broken
// backend costs a handful of requests a minute instead of sixty.
const POLL_MS = 1_000
const POLL_MAX_MS = 30_000

// Teachers see "headband" vs "camera", not raw source names like muse_optics.
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
        <span className="text-[11px] uppercase tracking-wider text-gray-500 font-bold dark:text-gray-400">{label}</span>
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

// No `rowKey`, so no table: a 60-point rolling window has no meaningful row
// labels, and tabulating it would be noise rather than an alternative. The
// summary is the reading.
const SPARK_COLUMNS = [
  { key: 'focus',      label: 'Focus',      unit: '%',    scale: asPercent },
  { key: 'engagement', label: 'Engagement', unit: '%',    scale: asPercent },
  { key: 'stress',     label: 'Stress',     unit: '%',    scale: asPercent },
  { key: 'bpm',        label: 'Heart rate', unit: ' bpm' },
]

function StudentCard({ student, history, now }) {
  const active = student.active_session
  const cog    = student.latest_cognitive
  const face   = student.latest_face
  const heart  = student.latest_heart
  const initial = (student.name || '?')[0].toUpperCase()

  // Age of the newest headband row. A binary on/off could not tell "just
  // went quiet" from "never connected", so a teacher watching a card go
  // blank had nothing to act on. `now` is a prop so every card ticks
  // together and a re-render is not needed per card per second.
  const cogAgeMs = cog?.ts ? now - Date.parse(cog.ts) : null
  const cogStale = cogAgeMs != null && cogAgeMs > STALE_AFTER_S * 1000
  const cogWeak  = eegWeak(cog)

  // Percentages, because the rolling window holds raw 0..1 ratios -- the chart
  // plots them against `domain={[0, 1]}`. Unscaled this said "Focus 0 to 1" for
  // every student on the page, whatever they were doing.
  //
  // Named per student: a screen-reader user reaching this card has no other way
  // to tell whose reading it is, since the heading is several elements back.

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
          <p className="text-[11px] text-gray-600 truncate dark:text-gray-400">{student.email}</p>
        </div>
        <span className={`text-[10px] font-bold px-2 py-1 rounded-full ${active ? 'bg-emerald-100 text-emerald-700 dark:bg-emerald-900/40 dark:text-emerald-300' : 'bg-gray-100 text-gray-600 dark:bg-gray-800 dark:text-gray-400'}`}>
          {active ? '● LIVE' : 'idle'}
        </span>
      </div>

      <div className="flex gap-2 mb-4 text-[10px] flex-wrap">
        {/* Three things on one badge, each shown rather than hidden: whether a
            row exists, how old it is, and whether it was usable. Grey for
            stale and for weak, like the heart badge, so the card doesn't
            look dead -- it looks like a headband that needs attention. */}
        <span className={`px-2 py-1 rounded-full font-bold flex items-center gap-1 ${
          cog && !cogStale && !cogWeak
            ? 'bg-indigo-100 text-indigo-700 dark:bg-indigo-900/40 dark:text-indigo-300'
            : 'bg-gray-100 text-gray-600 dark:bg-gray-800 dark:text-gray-300'}`}>
          <Brain size={11} /> Headband {cog ? 'on' : 'off'}
          {cog && cogAgeMs != null && ` · ${cogStale ? 'stale, ' : ''}${formatAge(cogAgeMs)}`}
          {cogWeak && ' · weak signal'}
        </span>
        <span className={`px-2 py-1 rounded-full font-bold flex items-center gap-1 ${face ? 'bg-pink-100 text-pink-700 dark:bg-pink-900/40 dark:text-pink-300' : 'bg-gray-100 text-gray-600 dark:bg-gray-800 dark:text-gray-300'}`}>
          <Camera size={11} /> Camera {face ? 'on' : 'off'}
        </span>
        {/* Names the sensor so a mid-session failover reads as a source change, not a student change.
            Shown as "weak signal" rather than hidden when untrusted, so the card doesn't look dead. */}
        {heart && (
          <span className={`px-2 py-1 rounded-full font-bold flex items-center gap-1 ${
            heart.trusted === false
              ? 'bg-gray-100 text-gray-600 dark:bg-gray-800 dark:text-gray-300'
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
      </div>

      {/* Shown as a number rather than on the 0..1 gauges above, since bpm isn't a ratio. */}
      {typeof heart?.heart_rate_bpm === 'number' && (
        <div className="flex items-center gap-2 mb-4 text-sm">
          <Heart size={16} className="text-purple-500" />
          <span className="font-bold text-gray-700 dark:text-gray-300">
            {Math.round(heart.heart_rate_bpm)} bpm
          </span>
          {typeof heart.rmssd_ms === 'number' && (
            <span className="text-xs text-gray-600 dark:text-gray-400">HRV {Math.round(heart.rmssd_ms)} ms</span>
          )}
        </div>
      )}

      {face?.emotion && (
        <div className="flex items-center gap-2 mb-4 text-sm">
          <span className="text-2xl">{EMOJI[face.emotion] || '🙂'}</span>
          <span className="capitalize font-bold text-gray-700 dark:text-gray-300">{face.emotion}</span>
        </div>
      )}

      {/* A sparkline is still a chart. It was a bare `<svg>` with no name, so
          the live monitor announced a student's card and then silence where
          the reading is. No data table: this is a 60-point rolling window with
          no meaningful row labels, and a table of it would be noise rather than
          the alternative the trend chart's is. The summary is the reading. */}
      <AccessibleChart className="h-12 -mx-1"
        headline={`${student.name || 'This student'}: signal trend over the last ${history?.length || 0} readings.`}
        rows={history} columns={SPARK_COLUMNS}>
          <LineChart data={history}>
            {/* Two axes because bpm and the 0..1 ratios would flatten together on one scale. */}
            <YAxis yAxisId="ratio" hide domain={[0, 1]} />
            <YAxis yAxisId="bpm" hide domain={['auto', 'auto']} />
            <Line yAxisId="ratio" type="monotone" dataKey="focus"      stroke="#6366f1" strokeWidth={1.5} dot={false} isAnimationActive={false} />
            <Line yAxisId="ratio" type="monotone" dataKey="engagement" stroke="#10b981" strokeWidth={1.5} dot={false} isAnimationActive={false} />
            <Line yAxisId="ratio" type="monotone" dataKey="stress"     stroke="#f43f5e" strokeWidth={1.5} dot={false} isAnimationActive={false} />
            <Line yAxisId="bpm"   type="monotone" dataKey="bpm"        stroke="#a855f7" strokeWidth={1.5} dot={false} connectNulls isAnimationActive={false} />
          </LineChart>
      </AccessibleChart>

      {active && (
        <Link
          to={`/teacher/sessions/${active.id}`}
          // So the session review's "Back" link returns here.
          state={{ from: '/teacher/live' }}
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
  // Which class `students` belongs to. `loading` is derived from it rather
  // than stored (CLAUDE.md, "derived loading"): switching class raises the
  // skeleton on the render that changes `classId`, so the previous class's
  // cards are never painted under this one's name -- which they were, for as
  // long as the new fetch took, and an unfetched class read as "Nobody's
  // joined yet". Seen driving the page by hand.
  const [loadedFor, setLoadedFor] = useState(null)
  const [error, setError]         = useState(null)
  // Separate from classes.length === 0, so loading doesn't briefly show "no classes yet".
  const [loadingClasses, setLoadingClasses] = useState(true)
  const historyRef = useRef({}) // user_id -> [{focus, engagement, stress}]
  // One clock for every card's "Xs ago", ticking whether or not a poll
  // landed -- a reading's age grows while the endpoint is failing too, and
  // that is exactly when a teacher needs to see it.
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(id)
  }, [])

  useEffect(() => {
    apiFetch('/api/classes')
      .then(rows => {
        setClasses(rows || [])
        if (rows?.length && !classId) setClassId(rows[0].id)
      })
      .catch(e => setError(e.message))
      .finally(() => setLoadingClasses(false))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    if (!classId) return
    let killed = false
    // Reset so switching class doesn't keep dragging along every student ever viewed.
    historyRef.current = {}

    let delay = POLL_MS
    let timer = null

    const tick = async () => {
      // Pause polling while the tab is hidden; visibilitychange below restarts it.
      if (document.hidden) {
        timer = setTimeout(tick, POLL_MS)
        return
      }
      try {
        // Deliberately ignores the "Hide sensor data" viewer switch -- that
        // control covers reporting surfaces, not this live monitor.
        const rows = await apiFetch(`/api/teacher/classes/${classId}/live`)
        if (killed) return
        rows.forEach(r => {
          const c = r.latest_cognitive
          const h = r.latest_heart
          // A student can consent to heart without EEG, so c and h can be
          // independently null -- only skip the tick if both are missing.
          if (!c && !h) return
          // Rebuilt, never mutated: React can freeze an array after it's
          // rendered, so pushing onto the old one would throw.
          const arr = historyRef.current[r.user_id] || []
          // Null (not 0) when a measurement is missing or its window was
          // rejected -- recharts leaves a gap for null instead of drawing a fake reading.
          const point = {
            focus:      c?.focus ?? null,
            engagement: c?.engagement ?? null,
            stress:     c?.stress ?? null,
            bpm:        typeof h?.heart_rate_bpm === 'number' ? h.heart_rate_bpm : null,
          }
          historyRef.current[r.user_id] = [...arr, point].slice(-60)
        })
        setStudents(rows)
        setLoadedFor(classId)
        delay = POLL_MS
        if (!killed) setError(null)
      } catch (e) {
        if (!killed) setError(e.message)
        delay = Math.min(delay * 2, POLL_MAX_MS)
      }
      // Chained timeout, not setInterval, so a slow response can't stack polls.
      if (!killed) timer = setTimeout(tick, delay)
    }

    // Resume at full speed when the tab becomes visible again, ignoring any backoff.
    const onVisible = () => {
      if (document.hidden || killed) return
      delay = POLL_MS
      clearTimeout(timer)
      tick()
    }
    document.addEventListener('visibilitychange', onVisible)

    tick()
    return () => {
      killed = true
      clearTimeout(timer)
      document.removeEventListener('visibilitychange', onVisible)
    }
  }, [classId])

  const loadingRoster = !!classId && loadedFor !== classId

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

      {loadingClasses ? (
        <SkeletonList count={3} height="h-28" />
      ) : !classes.length ? (
        <div className="text-center py-16">
          <div className="text-6xl mb-3">🏫</div>
          <p className="font-black text-gray-900 dark:text-white">No classes yet</p>
          <p className="text-sm text-gray-500 mt-1 dark:text-gray-400">Create a class first under the Classes tab.</p>
        </div>
      ) : loadingRoster ? (
        <SkeletonList count={3} height="h-28" />
      ) : students.length === 0 ? (
        <div className="text-center py-16">
          <div className="text-6xl mb-3">👀</div>
          <p className="font-black text-gray-900 dark:text-white">Nobody's joined yet</p>
          <p className="text-sm text-gray-500 mt-1 dark:text-gray-400">Share the join code so students can hop in.</p>
        </div>
      ) : (
        <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-3 gap-5">
          {students.map(s => (
            <StudentCard
              key={s.user_id}
              student={s}
              history={historyRef.current[s.user_id] || []}
              now={now}
            />
          ))}
        </div>
      )}
    </div>
  )
}