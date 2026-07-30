<<<<<<< HEAD
import { useCallback } from 'react'
import { useParams } from 'react-router-dom'
import { apiFetch } from '../../lib/api'
import StudentProgressReport from '../../components/reports/StudentProgressReport'

export default function ChildDetail() {
  const { id } = useParams()

  // Report-independent name source: the child's name comes from the children
  // list, so the heading survives a weekly-report failure. Memoised so it stays
  // stable across renders (the report runs it inside a studentId-keyed effect).
  const nameFetch = useCallback(
    () => apiFetch('/api/parent/children')
      .then(children => children.find(c => c.user_id === id)?.name || null),
    [id],
  )

  return (
    <StudentProgressReport
      // Remount on a new child id so the heading re-seeds from the name source
      // instead of showing the previous child's name until the fetch resolves.
      key={id}
      studentId={id}
      initialName="Child"
      backTo="/parent"
      backLabel="Back to Dashboard"
      backHoverClass="hover:text-emerald-600"
      emptyTopicText="No topic data yet — your child hasn't used AI Adaptive mode."
      nameFetch={nameFetch}
    />
=======
import { useEffect, useState } from 'react'
import { useParams, Link } from 'react-router-dom'
import { motion } from 'framer-motion'
import { ArrowLeft, BookOpen, Target, Flame, TrendingUp, Sparkles } from 'lucide-react'
import { apiFetch } from '../../lib/api'
import { FacialRecognitionToggle, LiveSignalSummary, StrategyPanel, WeeklySignalReport } from '../../components/signals/SignalPanel'

const TOPIC_ICONS = { ordering:'🔢', rationals:'➗', expressions:'📐', algebra:'🔣', geometry:'📏', angle_relationships:'📐', mean:'〰️', median:'📊', mode:'🔁', probability:'🎲' }

export default function ChildDetail() {
  const { id } = useParams()
  const [stats, setStats]         = useState(null)
  const [sessions, setSessions]   = useState([])
  const [perf, setPerf]           = useState([])
  const [report, setReport]       = useState(null)
  const [strategies, setStrategies] = useState(null)
  const [strategySource, setStrategySource] = useState(null)
  const [strategyLoading, setStrategyLoading] = useState(false)
  const [includeFace, setIncludeFace] = useState(() => localStorage.getItem('parent_include_face') !== 'false')
  const [loading, setLoading]     = useState(true)
  const [name, setName]           = useState('Child')

  useEffect(() => {
    localStorage.setItem('parent_include_face', includeFace ? 'true' : 'false')
    apiFetch(`/api/parent/children/${id}/weekly-report?include_face=${includeFace}`)
      .then(setReport)
      .catch(() => setReport(null))
  }, [id, includeFace])

  useEffect(() => {
    Promise.all([
      apiFetch(`/api/stats/student/${id}`),
      apiFetch(`/api/sessions/student/${id}`),
      apiFetch(`/api/performance/student/${id}`),
      apiFetch(`/api/parent/children/${id}/weekly-report?include_face=${includeFace}`),
    ]).then(([s, sess, p, weekly]) => {
      setStats(s)
      setSessions(sess || [])
      setPerf(p || [])
      setReport(weekly || null)
      setLoading(false)
    }).catch(() => setLoading(false))

    apiFetch('/api/parent/children').then(children => {
      const child = children.find(c => c.user_id === id)
      if (child) setName(child.name)
    }).catch(() => {})
  }, [id])

  async function generateStrategies() {
    setStrategyLoading(true)
    setStrategies(null)
    try {
      const res = await apiFetch(`/api/parent/children/${id}/learning-strategies`, {
        method: 'POST',
        body: { include_face: includeFace }
      })
      setStrategies(res.strategies || [])
      setStrategySource(res.source || null)
      if (res.report) setReport(res.report)
    } catch {
      setStrategies(['Could not generate strategies right now. Try again after the backend and Ollama are running.'])
      setStrategySource('error fallback')
    } finally {
      setStrategyLoading(false)
    }
  }

  const acc = stats?.total_questions > 0 ? Math.round((stats.total_correct / stats.total_questions) * 100) : 0
  const latestCognitive = report?.eeg?.latest
  const latestFace = includeFace ? report?.face?.latest : null

  return (
    <div className="p-6 lg:p-8 pb-12">
      <motion.div initial={{ opacity: 0, y: -10 }} animate={{ opacity: 1, y: 0 }} className="mb-6">
        <Link to="/parent" className="flex items-center gap-2 text-sm text-gray-500 dark:text-gray-400 hover:text-emerald-600 mb-3 transition font-semibold w-fit">
          <ArrowLeft size={16} /> Back to Dashboard
        </Link>
        <h1 className="text-3xl font-black text-gray-900 dark:text-white">{name}'s Progress</h1>
        <p className="text-gray-500 dark:text-gray-400 mt-1">Full learning report with weekly performance, EEG, and facial-signal summaries.</p>
      </motion.div>

      {loading ? (
        <div className="space-y-4">{[1,2,3].map(i => <div key={i} className="h-32 bg-white dark:bg-gray-900 rounded-2xl border border-gray-100 dark:border-gray-800 animate-pulse" />)}</div>
      ) : (
        <div className="space-y-6">
          <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
            {[
              { icon: BookOpen,   label: 'Questions',  value: stats?.total_questions ?? 0,  color: 'bg-gradient-to-br from-indigo-500 to-indigo-600' },
              { icon: Target,     label: 'Correct',    value: stats?.total_correct ?? 0,    color: 'bg-gradient-to-br from-green-500 to-emerald-600' },
              { icon: TrendingUp, label: 'Accuracy',   value: `${acc}%`,                    color: 'bg-gradient-to-br from-violet-500 to-purple-600' },
              { icon: Flame,      label: 'Streak',     value: `${stats?.current_streak ?? 0}d`, color: 'bg-gradient-to-br from-orange-500 to-amber-500' },
            ].map((c, i) => (
              <motion.div key={c.label} initial={{ opacity: 0, y: 16 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: i * 0.08 }}
                whileHover={{ y: -3 }}
                className="bg-white dark:bg-gray-900 rounded-2xl border border-gray-100 dark:border-gray-800 p-5 shadow-sm flex items-start justify-between">
                <div>
                  <p className="text-xs font-semibold uppercase tracking-widest text-gray-400 mb-1">{c.label}</p>
                  <p className="text-3xl font-black text-gray-900 dark:text-white">{c.value}</p>
                </div>
                <div className={`p-2.5 ${c.color} rounded-xl shadow-md`}>
                  <c.icon size={18} className="text-white" />
                </div>
              </motion.div>
            ))}
          </div>

          <FacialRecognitionToggle enabled={includeFace} onChange={setIncludeFace} />
          <LiveSignalSummary cognitive={latestCognitive} face={latestFace} includeFace={includeFace} />
          <WeeklySignalReport report={report} includeFace={includeFace} title="Weekly Student Performance, EEG & Face Report" />

          <div className="bg-white dark:bg-gray-900 rounded-2xl border border-gray-100 dark:border-gray-800 p-5 shadow-sm">
            <div className="flex items-center justify-between gap-3 flex-wrap">
              <div>
                <h3 className="font-black text-gray-900 dark:text-white">AI At-Home Strategy Support</h3>
                <p className="text-xs text-gray-500 dark:text-gray-400 mt-1">Generates parent-friendly practice ideas based on the weekly report. Includes a safe fallback if the local AI is unavailable.</p>
              </div>
              <button onClick={generateStrategies} disabled={strategyLoading}
                className="inline-flex items-center gap-2 px-4 py-2.5 rounded-xl bg-violet-600 hover:bg-violet-700 disabled:opacity-60 text-white text-sm font-bold shadow transition">
                <Sparkles size={16} /> {strategyLoading ? 'Generating...' : 'Generate Strategies'}
              </button>
            </div>
          </div>
          <StrategyPanel strategies={strategies} source={strategySource} />

          <div className="grid lg:grid-cols-2 gap-6">
            <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ delay: 0.35 }}
              className="bg-white dark:bg-gray-900 rounded-2xl border border-gray-100 dark:border-gray-800 p-5 shadow-sm">
              <h3 className="font-black text-gray-900 dark:text-white mb-5">Topic Performance</h3>
              {perf.length === 0 ? (
                <p className="text-gray-400 text-sm text-center py-6">No topic data yet — your child hasn't used AI Adaptive mode.</p>
              ) : (
                <div className="space-y-3">
                  {perf.map(p => {
                    const topicName = p.math_topics?.topic_name || 'unknown'
                    const topicAcc  = p.attempted_questions > 0 ? Math.round((p.correct_questions / p.attempted_questions) * 100) : 0
                    return (
                      <div key={p.topic_id}>
                        <div className="flex items-center justify-between mb-1">
                          <span className="text-sm font-medium text-gray-700 dark:text-gray-300 flex items-center gap-1.5">
                            {TOPIC_ICONS[topicName] || '📘'} <span className="capitalize">{topicName.replace('_', ' ')}</span>
                          </span>
                          <span className={`text-xs font-black ${topicAcc >= 70 ? 'text-green-600' : topicAcc >= 40 ? 'text-amber-600' : 'text-rose-600'}`}>{topicAcc}%</span>
                        </div>
                        <div className="h-2 bg-gray-100 dark:bg-gray-700 rounded-full overflow-hidden">
                          <motion.div className={`h-full rounded-full ${topicAcc >= 70 ? 'bg-green-500' : topicAcc >= 40 ? 'bg-amber-500' : 'bg-rose-500'}`}
                            initial={{ width: 0 }} animate={{ width: `${topicAcc}%` }} transition={{ duration: 0.6 }} />
                        </div>
                        <p className="text-xs text-gray-400 mt-0.5">{p.correct_questions}/{p.attempted_questions} correct</p>
                      </div>
                    )
                  })}
                </div>
              )}
            </motion.div>

            <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ delay: 0.4 }}
              className="bg-white dark:bg-gray-900 rounded-2xl border border-gray-100 dark:border-gray-800 p-5 shadow-sm">
              <h3 className="font-black text-gray-900 dark:text-white mb-5">Recent Sessions</h3>
              {sessions.length === 0 ? (
                <p className="text-gray-400 text-sm text-center py-6">No sessions yet.</p>
              ) : (
                <div className="space-y-2">
                  {sessions.map((s, i) => {
                    const sAcc = s.questions_answered > 0 ? Math.round((s.correct_answers / s.questions_answered) * 100) : 0
                    return (
                      <motion.div key={s.id} initial={{ opacity: 0, x: -8 }} animate={{ opacity: 1, x: 0 }} transition={{ delay: i * 0.04 }}
                        className="flex items-center justify-between p-3 bg-slate-50 dark:bg-gray-800 rounded-xl">
                        <div>
                          <p className="text-sm font-semibold text-gray-900 dark:text-white">{s.title || 'Practice Session'}</p>
                          <p className="text-xs text-gray-400">{new Date(s.started_at).toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' })}</p>
                        </div>
                        <div className="text-right">
                          <p className={`text-sm font-black ${sAcc >= 70 ? 'text-green-500' : sAcc >= 40 ? 'text-amber-500' : 'text-rose-500'}`}>{sAcc}%</p>
                          <p className="text-xs text-gray-400">{s.questions_answered}q</p>
                        </div>
                      </motion.div>
                    )
                  })}
                </div>
              )}
            </motion.div>
          </div>
        </div>
      )}
    </div>
>>>>>>> 4fa1ce3 (Add parent reports and signal safety features)
  )
}
