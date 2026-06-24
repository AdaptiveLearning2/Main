import { useEffect, useState } from 'react'
import { useParams, Link } from 'react-router-dom'
import { motion } from 'framer-motion'
import { ArrowLeft, Brain, Camera, CheckCircle2, XCircle, Activity } from 'lucide-react'
import {
  LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer,
  CartesianGrid, ReferenceLine, Legend
} from 'recharts'
import { apiFetch } from '../../lib/api'

const EMOJI = { happy: '😀', neutral: '😐', confused: '😕', frustrated: '😤', sad: '😢', surprised: '😮', angry: '😠' }

function fmtTime(ms) {
  if (!Number.isFinite(ms)) return ''
  const d = new Date(ms)
  return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })
}

export default function SessionReview() {
  const { sessionId } = useParams()
  const [data, setData] = useState(null)
  const [err, setErr]   = useState(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    let killed = false
    setLoading(true)
    apiFetch(`/api/signals/session/${sessionId}`)
      .then(d => { if (!killed) setData(d) })
      .catch(e => { if (!killed) setErr(e.message || String(e)) })
      .finally(() => { if (!killed) setLoading(false) })
    return () => { killed = true }
  }, [sessionId])

  if (loading) {
    return (
      <div className="p-8 text-center text-gray-500">
        <motion.div animate={{ rotate: 360 }} transition={{ duration: 1, repeat: Infinity, ease: 'linear' }}
          className="w-10 h-10 border-4 border-violet-600 border-t-transparent rounded-full mx-auto mb-3" />
        Loading session…
      </div>
    )
  }

  if (err) {
    return (
      <div className="p-8">
        <Link to="/teacher/live" className="text-sm text-violet-600 font-bold flex items-center gap-1 mb-4">
          <ArrowLeft size={14} /> Back
        </Link>
        <div className="bg-rose-50 dark:bg-rose-900/20 border border-rose-200 dark:border-rose-800 rounded-2xl p-6">
          <p className="font-black text-rose-700 dark:text-rose-300 mb-1">Could not load session</p>
          <p className="text-sm text-rose-600 dark:text-rose-400 break-all">{err}</p>
        </div>
      </div>
    )
  }

  const cognitive = Array.isArray(data?.cognitive) ? data.cognitive : []
  const face      = Array.isArray(data?.face)      ? data.face      : []
  const answers   = Array.isArray(data?.answers)   ? data.answers   : []

  // numeric ms x-axis — way more stable than category strings
  const series = cognitive
    .map(c => {
      const t = new Date(c.ts).getTime()
      return Number.isFinite(t) ? {
        t,
        focus:      typeof c.focus      === 'number' ? c.focus      : null,
        engagement: typeof c.engagement === 'number' ? c.engagement : null,
        stress:     typeof c.stress     === 'number' ? c.stress     : null,
      } : null
    })
    .filter(Boolean)
    .sort((a, b) => a.t - b.t)

  const tMin = series.length ? series[0].t : 0
  const tMax = series.length ? series[series.length - 1].t : 0

  // emotion ribbon — bucket every ~10s
  const ribbon = []
  let lastBucket = 0
  face.forEach(f => {
    const t = new Date(f.ts).getTime()
    if (Number.isFinite(t) && t - lastBucket > 10_000) {
      ribbon.push({ t, emotion: f.emotion, attention: f.attention })
      lastBucket = t
    }
  })

  const totalAnswers   = answers.length
  const correctAnswers = answers.filter(a => a.correct).length
  const acc = totalAnswers ? Math.round((correctAnswers / totalAnswers) * 100) : 0

  const hasChart = series.length >= 2

  return (
    <div className="p-6 lg:p-8 pb-12 space-y-6">
      <Link to="/teacher/live" className="text-sm text-violet-600 font-bold flex items-center gap-1 hover:text-violet-700">
        <ArrowLeft size={14} /> Back to live
      </Link>

      <motion.div initial={{ opacity: 0, y: -8 }} animate={{ opacity: 1, y: 0 }}>
        <h1 className="text-3xl font-black text-gray-900 dark:text-white">Session Review</h1>
        <p className="text-gray-500 dark:text-gray-400 mt-1 text-sm font-mono break-all">id: {sessionId}</p>
      </motion.div>

      {/* summary tiles */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        {[
          { label: 'Cognitive samples', value: cognitive.length, icon: <Brain size={16} className="text-indigo-500" /> },
          { label: 'Face samples',      value: face.length,      icon: <Camera size={16} className="text-pink-500" /> },
          { label: 'Answers',           value: totalAnswers,     icon: <Activity size={16} className="text-emerald-500" /> },
          { label: 'Accuracy',          value: totalAnswers ? `${acc}%` : '—', icon: <CheckCircle2 size={16} className="text-violet-500" /> },
        ].map(t => (
          <div key={t.label} className="bg-white dark:bg-gray-900 rounded-2xl border border-gray-100 dark:border-gray-800 p-4 shadow-sm">
            <div className="flex items-center gap-2 mb-1">{t.icon}<span className="text-[11px] uppercase tracking-wider text-gray-400 font-bold">{t.label}</span></div>
            <div className="text-2xl font-black text-gray-900 dark:text-white">{t.value}</div>
          </div>
        ))}
      </div>

      {/* main timeseries chart */}
      <div className="bg-white dark:bg-gray-900 rounded-2xl border border-gray-100 dark:border-gray-800 shadow-sm p-5">
        <h2 className="font-black text-gray-900 dark:text-white mb-4 flex items-center gap-2">
          <Brain size={18} className="text-indigo-600" /> Cognitive timeline
        </h2>
        {!hasChart ? (
          <div className="text-center py-12">
            <div className="text-5xl mb-2">🧠</div>
            <p className="text-sm text-gray-400">No cognitive samples for this session yet.</p>
            <p className="text-[11px] text-gray-400 mt-1">Once the headband starts streaming, it'll show up here.</p>
          </div>
        ) : (
          <div className="h-72">
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={series} margin={{ top: 5, right: 20, left: 0, bottom: 5 }}>
                <CartesianGrid strokeDasharray="3 3" opacity={0.3} />
                <XAxis
                  dataKey="t"
                  type="number"
                  domain={[tMin, tMax]}
                  scale="time"
                  tickFormatter={fmtTime}
                  fontSize={10}
                  minTickGap={50}
                />
                <YAxis domain={[0, 1]} fontSize={10} />
                <Tooltip
                  labelFormatter={(v) => fmtTime(v)}
                  formatter={(v) => (typeof v === 'number' ? v.toFixed(2) : v)}
                />
                <Legend wrapperStyle={{ fontSize: 12 }} />
                <Line type="monotone" dataKey="focus"      stroke="#6366f1" dot={false} connectNulls isAnimationActive={false} />
                <Line type="monotone" dataKey="engagement" stroke="#10b981" dot={false} connectNulls isAnimationActive={false} />
                <Line type="monotone" dataKey="stress"     stroke="#f43f5e" dot={false} connectNulls isAnimationActive={false} />

                {/* answer markers as vertical reference lines (numeric x is safe) */}
                {answers.map((a, i) => {
                  const x = new Date(a.answered_at).getTime()
                  if (!Number.isFinite(x) || x < tMin || x > tMax) return null
                  return (
                    <ReferenceLine
                      key={i}
                      x={x}
                      stroke={a.correct ? '#10b981' : '#f43f5e'}
                      strokeDasharray="3 3"
                      strokeOpacity={0.7}
                    />
                  )
                })}
              </LineChart>
            </ResponsiveContainer>
          </div>
        )}
        {hasChart && answers.length > 0 && (
          <p className="text-[11px] text-gray-400 mt-2">
            Vertical lines = answer events · <span className="text-emerald-500">green</span> correct ·{' '}
            <span className="text-rose-500">red</span> incorrect
          </p>
        )}
      </div>

      {/* emotion ribbon */}
      <div className="bg-white dark:bg-gray-900 rounded-2xl border border-gray-100 dark:border-gray-800 shadow-sm p-5">
        <h2 className="font-black text-gray-900 dark:text-white mb-4 flex items-center gap-2">
          <Camera size={18} className="text-pink-600" /> Emotion timeline
        </h2>
        {ribbon.length === 0 ? (
          <div className="text-center py-8">
            <div className="text-4xl mb-2">📷</div>
            <p className="text-sm text-gray-400">No face samples for this session.</p>
          </div>
        ) : (
          <div className="flex gap-1 overflow-x-auto pb-2">
            {ribbon.map((r, i) => (
              <div key={i} title={`${fmtTime(r.t)} — ${r.emotion || 'unknown'}`}
                   className="flex flex-col items-center text-xs flex-shrink-0 w-14">
                <span className="text-2xl">{EMOJI[r.emotion] || '🙂'}</span>
                <span className="text-[9px] text-gray-400 mt-0.5">{fmtTime(r.t)}</span>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* answers table */}
      <div className="bg-white dark:bg-gray-900 rounded-2xl border border-gray-100 dark:border-gray-800 shadow-sm overflow-hidden">
        <h2 className="font-black text-gray-900 dark:text-white px-5 pt-5 mb-3">Answers</h2>
        {answers.length === 0 ? (
          <p className="px-5 pb-5 text-sm text-gray-400">No answers recorded.</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="text-left text-[11px] uppercase tracking-wider text-gray-400">
                <tr>
                  <th className="px-5 py-2 font-bold">Time</th>
                  <th className="font-bold">Question ID</th>
                  <th className="font-bold">Pick</th>
                  <th className="font-bold pr-5">Result</th>
                </tr>
              </thead>
              <tbody>
                {answers.map((a, i) => {
                  const t = new Date(a.answered_at).getTime()
                  return (
                    <tr key={i} className="border-t border-gray-50 dark:border-gray-800">
                      <td className="px-5 py-2 text-gray-500 whitespace-nowrap">{fmtTime(t)}</td>
                      <td className="text-gray-700 dark:text-gray-300 font-mono text-xs">{(a.question_id || '').slice(0, 12)}…</td>
                      <td className="text-gray-700 dark:text-gray-300">{a.selected_index ?? '—'}</td>
                      <td className="pr-5">
                        {a.correct
                          ? <span className="text-emerald-500 flex items-center gap-1"><CheckCircle2 size={14} /> correct</span>
                          : <span className="text-rose-500 flex items-center gap-1"><XCircle size={14} /> wrong</span>}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  )
}