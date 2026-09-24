import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { motion } from 'framer-motion'
import { ArrowLeft, BookOpen, Target, Flame, TrendingUp } from 'lucide-react'
import { WeeklySignalReport, SignalTrend, LiveSignalSummary, StrategyPanel, ChartSummaryPanel } from '../signals/SignalPanel'
import { apiFetch } from '../../lib/api'
import FocusAccuracy from '../analytics/FocusAccuracy'
import { useLatestRequest } from '../../hooks/useLatestRequest'
import { TOPIC_ICONS as ICONS, topicLabel } from '../../lib/topics'

const TOPIC_ICONS = ICONS

/**
 * A single student's full learning report, shared by parent ChildDetail and teacher StudentReport.
 * `nameFetch` (optional) owns the name when given; otherwise it comes from the weekly report.
 * `showSignals` is the teacher's "Hide sensor data" preference: rendering only, not a privacy boundary.
 * `viewerRole` ('parent' | 'teacher') frames panel copy only.
 */
export default function StudentProgressReport({
  studentId,
  initialName = 'Student',
  backTo,
  backLabel,
  backHoverClass = 'hover:text-violet-600',
  emptyTopicText = 'No topic data yet.',
  nameFetch,
  showStrategies = false,
  showChartSummary = false,
  showSignals = true,
  viewerRole = 'parent',
}) {
  const [stats, setStats]         = useState(null)
  const [sessions, setSessions]   = useState([])
  const [perf, setPerf]           = useState([])
  const [loading, setLoading]     = useState(true)
  const [name, setName]           = useState(initialName)
  const [signalReport, setSignalReport] = useState(null)
  const [signalError, setSignalError]   = useState(null)
  // No error twin: a failed read is `retrieved: false` on the payload.
  const [trend, setTrend]               = useState(null)
  const [focusAccuracy, setFocusAccuracy] = useState(null)
  const [loadError, setLoadError]       = useState(null)
  const [strategies, setStrategies]     = useState(null)
  const [strategySource, setStrategySource]   = useState(null)
  // Whether the aggregate behind the advice loaded.
  const [strategySignals, setStrategySignals] = useState(null)
  const [strategyError, setStrategyError]     = useState(null)
  const [strategyLoading, setStrategyLoading] = useState(false)
  const beginStrategyRequest = useLatestRequest()

  // Separate state and guard from strategies: two buttons, pressed in any order.
  const [chartSummary, setChartSummary]             = useState(null)
  const [chartSummarySource, setChartSummarySource] = useState(null)
  const [chartSummaryRetrieved, setChartSummaryRetrieved] = useState(null)
  const [chartSummaryError, setChartSummaryError]   = useState(null)
  const [chartSummaryLoading, setChartSummaryLoading] = useState(false)
  const beginChartSummaryRequest = useLatestRequest()

  useEffect(() => {
    Promise.all([
      apiFetch(`/api/stats/student/${studentId}`),
      apiFetch(`/api/sessions/student/${studentId}`),
      apiFetch(`/api/performance/student/${studentId}`),
    ]).then(([s, sess, p]) => {
      setStats(s)
      setSessions(sess || [])
      setPerf(p || [])
      setLoadError(null)
      setLoading(false)
    }).catch(err => {
      // Never fall through to a zeros-filled report.
      setLoadError(err.message || 'Could not load this report')
      setLoading(false)
    })

    if (nameFetch) {
      nameFetch().then(n => { if (n) setName(n) }).catch(() => {})
    }
  }, [studentId, nameFetch])

  // Separate so a failure here doesn't blank the page. Callers memoise nameFetch per id.
  useEffect(() => {
    let cancelled = false
    apiFetch(`/api/students/${studentId}/weekly-report`)
      .then(r => {
        if (cancelled) return
        setSignalReport(r)
        setSignalError(null)
        if (!nameFetch && r?.student_name) setName(r.student_name)
      })
      // Distinct from "no report", which is a quiet week.
      .catch(err => {
        if (cancelled) return
        setSignalReport(null)
        setSignalError(err.message || 'Could not load signal report')
      })
    return () => { cancelled = true }
  }, [studentId, nameFetch])

  // A thrown request gets a `retrieved: false` payload; null would read as loading forever.
  useEffect(() => {
    let cancelled = false
    apiFetch(`/api/students/${studentId}/signal-trend`)
      .then(r => { if (!cancelled) setTrend(r) })
      .catch(() => { if (!cancelled) setTrend({ weeks: [], retrieved: false }) })
    return () => { cancelled = true }
  }, [studentId])

  // Same as the trend above.
  useEffect(() => {
    let cancelled = false
    apiFetch(`/api/students/${studentId}/focus-accuracy`)
      .then(r => { if (!cancelled) setFocusAccuracy(r) })
      .catch(() => { if (!cancelled) setFocusAccuracy({ buckets: [], retrieved: false }) })
    return () => { cancelled = true }
  }, [studentId])



  async function generateStrategies() {
    const isCurrent = beginStrategyRequest()
    setStrategyLoading(true)
    setStrategyError(null)
    try {
      // FastAPI 422s a bodyless POST even when every field defaults.
      const res = await apiFetch(`/api/students/${studentId}/learning-strategies`, {
        method: 'POST',
        body: {},
      })
      if (!isCurrent()) return
      setStrategies(res.strategies || [])
      setStrategySource(res.source || null)
      setStrategySignals(res.basis?.signals_retrieved ?? null)
    } catch (err) {
      if (!isCurrent()) return
      setStrategies(null)
      setStrategySource(null)
      setStrategySignals(null)
      setStrategyError(err.message || 'Could not generate strategies right now.')
    } finally {
      // Only the newest request owns the spinner.
      if (isCurrent()) setStrategyLoading(false)
    }
  }

  async function generateChartSummary() {
    const isCurrent = beginChartSummaryRequest()
    setChartSummaryLoading(true)
    setChartSummaryError(null)
    try {
      const res = await apiFetch(`/api/students/${studentId}/chart-summary`, {
        method: 'POST',
        body: {},
      })
      if (!isCurrent()) return
      setChartSummary(res.summary || [])
      setChartSummarySource(res.source || null)
      // Undefined where absent; the panel checks `=== false`.
      setChartSummaryRetrieved({
        signals: res.basis?.signals_retrieved,
        trend: res.basis?.trend_retrieved,
        stats: res.basis?.stats_retrieved,
        topics: res.basis?.topics_retrieved,
      })
    } catch (err) {
      if (!isCurrent()) return
      setChartSummary(null)
      setChartSummarySource(null)
      setChartSummaryRetrieved(null)
      setChartSummaryError(err.message || 'Could not generate a summary right now.')
    } finally {
      if (isCurrent()) setChartSummaryLoading(false)
    }
  }

  const acc = stats?.total_questions > 0 ? Math.round((stats.total_correct / stats.total_questions) * 100) : 0

  return (
    <div className="p-6 lg:p-8 pb-12">
      <motion.div initial={{ opacity: 0, y: -10 }} animate={{ opacity: 1, y: 0 }} className="mb-6">
        <Link to={backTo} className={`flex items-center gap-2 text-sm text-gray-500 dark:text-gray-400 ${backHoverClass} mb-3 transition font-semibold w-fit`}>
          <ArrowLeft size={16} /> {backLabel}
        </Link>
        <h1 className="text-3xl font-black text-gray-900 dark:text-white">{name}'s Progress</h1>
        <p className="text-gray-500 dark:text-gray-400 mt-1">Full learning report.</p>
      </motion.div>

      {loading ? (
        <div className="space-y-4">{[1,2,3].map(i => <div key={i} className="h-32 bg-white dark:bg-gray-900 rounded-2xl border border-gray-100 dark:border-gray-800 animate-pulse" />)}</div>
      ) : loadError ? (
        <div className="rounded-2xl border border-gray-100 dark:border-gray-800 bg-white dark:bg-gray-900 p-8 shadow-sm text-center">
          <p className="text-sm font-semibold text-gray-600 dark:text-gray-300">Couldn&apos;t load this student&apos;s report.</p>
          <p className="text-xs text-gray-600 mt-1 dark:text-gray-400">{loadError}</p>
        </div>
      ) : (
        <div className="space-y-6">
          {/* stat cards */}
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
                  <p className="text-xs font-semibold uppercase tracking-widest text-gray-600 mb-1 dark:text-gray-400">{c.label}</p>
                  <p className="text-3xl font-black text-gray-900 dark:text-white">{c.value}</p>
                </div>
                <div className={`p-2.5 ${c.color} rounded-xl shadow-md`}>
                  <c.icon size={18} className="text-white" />
                </div>
              </motion.div>
            ))}
          </div>

          {/* Only once loaded; a grid of "N/A" would read as no activity. */}
          {showSignals && signalError && (
            <div className="rounded-2xl border border-gray-100 dark:border-gray-800 bg-white dark:bg-gray-900 p-5 shadow-sm text-center">
              <p className="text-sm text-gray-500 dark:text-gray-400">Couldn&apos;t load the EEG &amp; face report.</p>
              <p className="text-xs text-gray-600 mt-1 dark:text-gray-400">{signalError}</p>
            </div>
          )}
          {showSignals && signalReport && (
            <div className="grid lg:grid-cols-1 gap-6">
              <LiveSignalSummary report={signalReport} title="Latest Signal Snapshot" />
              <WeeklySignalReport report={signalReport} title="Weekly EEG & Face Report" />
            </div>
          )}

          {/* Own gate: a different endpoint from the weekly report. */}
          {showSignals && trend && <SignalTrend trend={trend} />}

          {showSignals && focusAccuracy && (
            <FocusAccuracy data={focusAccuracy} />
          )}

          {showChartSummary && (
            <ChartSummaryPanel
              summary={chartSummary}
              source={chartSummarySource}
              retrieved={chartSummaryRetrieved}
              loading={chartSummaryLoading}
              error={chartSummaryError}
              onGenerate={generateChartSummary}
              viewerRole={viewerRole}
            />
          )}

          {showStrategies && (
            <StrategyPanel
              strategies={strategies}
              source={strategySource}
              signalsRetrieved={strategySignals}
              loading={strategyLoading}
              error={strategyError}
              onGenerate={generateStrategies}
              viewerRole={viewerRole}
            />
          )}

          <div className="grid lg:grid-cols-2 gap-6">
            {/* topic performance */}
            <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ delay: 0.35 }}
              className="bg-white dark:bg-gray-900 rounded-2xl border border-gray-100 dark:border-gray-800 p-5 shadow-sm">
              <h3 className="font-black text-gray-900 dark:text-white mb-5">Topic Performance</h3>
              {perf.length === 0 ? (
                <p className="text-gray-600 text-sm text-center py-6 dark:text-gray-400">{emptyTopicText}</p>
              ) : (
                <div className="space-y-3">
                  {perf.map(p => {
                    const topicName = p.math_topics?.topic_name || 'unknown'
                    const topicAcc  = p.attempted_questions > 0 ? Math.round((p.correct_questions / p.attempted_questions) * 100) : 0
                    return (
                      <div key={p.topic_id}>
                        <div className="flex items-center justify-between mb-1">
                          <span className="text-sm font-medium text-gray-700 dark:text-gray-300 flex items-center gap-1.5">
                            {TOPIC_ICONS[topicName] || '📘'} <span className="capitalize">{topicLabel(topicName)}</span>
                          </span>
                          <span className={`text-xs font-black ${topicAcc >= 70 ? 'text-green-600' : topicAcc >= 40 ? 'text-amber-600' : 'text-rose-600'}`}>{topicAcc}%</span>
                        </div>
                        <div className="h-2 bg-gray-100 dark:bg-gray-700 rounded-full overflow-hidden">
                          <motion.div className={`h-full rounded-full ${topicAcc >= 70 ? 'bg-green-500' : topicAcc >= 40 ? 'bg-amber-500' : 'bg-rose-500'}`}
                            initial={{ width: 0 }} animate={{ width: `${topicAcc}%` }} transition={{ duration: 0.6 }} />
                        </div>
                        <p className="text-xs text-gray-600 mt-0.5 dark:text-gray-400">{p.correct_questions}/{p.attempted_questions} correct</p>
                      </div>
                    )
                  })}
                </div>
              )}
            </motion.div>

            {/* session history */}
            <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ delay: 0.4 }}
              className="bg-white dark:bg-gray-900 rounded-2xl border border-gray-100 dark:border-gray-800 p-5 shadow-sm">
              <h3 className="font-black text-gray-900 dark:text-white mb-5">Recent Sessions</h3>
              {sessions.length === 0 ? (
                <p className="text-gray-600 text-sm text-center py-6 dark:text-gray-400">No sessions yet.</p>
              ) : (
                <div className="space-y-2">
                  {sessions.map((s, i) => {
                    const sAcc = s.questions_answered > 0 ? Math.round((s.correct_answers / s.questions_answered) * 100) : 0
                    return (
                      <motion.div key={s.id} initial={{ opacity: 0, x: -8 }} animate={{ opacity: 1, x: 0 }} transition={{ delay: i * 0.04 }}
                        className="flex items-center justify-between p-3 bg-slate-50 dark:bg-gray-800 rounded-xl">
                        <div>
                          <p className="text-sm font-semibold text-gray-900 dark:text-white">{s.title || 'Practice Session'}</p>
                          <p className="text-xs text-gray-600 dark:text-gray-400">{new Date(s.started_at).toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' })}</p>
                        </div>
                        <div className="text-right">
                          <p className={`text-sm font-black ${sAcc >= 70 ? 'text-green-500' : sAcc >= 40 ? 'text-amber-500' : 'text-rose-500'}`}>{sAcc}%</p>
                          <p className="text-xs text-gray-600 dark:text-gray-400">{s.questions_answered}q</p>
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
  )
}
