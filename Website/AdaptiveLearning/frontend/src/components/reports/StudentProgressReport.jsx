import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { motion } from 'framer-motion'
import { ArrowLeft, BookOpen, Target, Flame, TrendingUp } from 'lucide-react'
import { WeeklySignalReport, SignalTrend, LiveSignalSummary, StrategyPanel } from '../signals/SignalPanel'
import { apiFetch } from '../../lib/api'
import FocusAccuracy from '../analytics/FocusAccuracy'
import { useLatestRequest } from '../../hooks/useLatestRequest'
// Persisted so the choice survives navigation between students. Shared with
// the teacher student list, which reads the same facial signals.

const TOPIC_ICONS = { ordering:'🔢', missing_number:'❓', patterns:'📶', graphs:'📊', shape_fractions:'🥧', rationals:'➗', expressions:'📐', algebra:'🔣', geometry:'📏', angle_relationships:'📐', mean:'〰️', median:'📊', mode:'🔁', probability:'🎲' }

/**
 * A single student's full learning report: academic stat cards, the weekly
 * EEG/face signal panels, topic performance, and recent sessions.
 *
 * Shared by the parent-facing ChildDetail page and the teacher-facing
 * StudentReport page, which differ only in navigation chrome, one copy string,
 * and how the display name is resolved.
 *
 * @param {string}   studentId       user_id whose report to load.
 * @param {string}   initialName     name shown until a better one resolves.
 * @param {string}   backTo          route for the "back" link.
 * @param {string}   backLabel       text for the "back" link.
 * @param {string}   backHoverClass  Tailwind hover colour for the "back" link.
 * @param {string}   emptyTopicText  shown when the student has no topic data yet.
 * @param {Function} [nameFetch]     optional independent name source returning a
 *   Promise<string|null>. When provided it owns the name (the parent's children
 *   list, which survives a weekly-report failure); when omitted the name comes
 *   from the weekly-report's student_name.
 * @param {boolean}  [showStrategies] render the at-home strategies panel. Parent
 *   route only -- the copy is written for someone supporting a child at home.
 */
/**
 * `showSignals` is the teacher's *"Hide sensor data"* view preference, passed
 * in rather than read here since the parent surface has no such switch.
 * Defaults to true so a caller that knows nothing about the filter renders
 * everything. It hides rendering only -- what may be read is decided by
 * stored consent server-side; see `lib/viewPrefs.js`.
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
  showSignals = true,
}) {
  const [stats, setStats]         = useState(null)
  const [sessions, setSessions]   = useState([])
  const [perf, setPerf]           = useState([])
  const [loading, setLoading]     = useState(true)
  const [name, setName]           = useState(initialName)
  const [signalReport, setSignalReport] = useState(null)
  const [signalError, setSignalError]   = useState(null)
  // Its own state, and no error twin: the trend carries `retrieved`, so a
  // failed read is a state of the payload rather than the absence of one.
  const [trend, setTrend]               = useState(null)
  const [focusAccuracy, setFocusAccuracy] = useState(null)
  const [loadError, setLoadError]       = useState(null)
  const [strategies, setStrategies]     = useState(null)
  const [strategySource, setStrategySource]   = useState(null)
  // Whether the aggregate the advice was derived from actually loaded. Without
  // it the panel would present a generic list as if built from the child's
  // week, since a null average just falls through to the generic rules.
  const [strategySignals, setStrategySignals] = useState(null)
  const [strategyError, setStrategyError]     = useState(null)
  const [strategyLoading, setStrategyLoading] = useState(false)
  // Guards against a stale generation response overwriting a newer one.
  // Same helper as Sessions.jsx's roster read -- see hooks/useLatestRequest.
  const beginStrategyRequest = useLatestRequest()

  // Academic stats and the name. Not re-run when the facial toggle flips --
  // none of this depends on it.
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
      // A failed core load must not fall through to the zeros-filled report --
      // that would read as a real-but-inactive student and, on the teacher
      // route, hide that the id simply isn't theirs to view.
      setLoadError(err.message || 'Could not load this report')
      setLoading(false)
    })

    // Optional independent name source (the parent's children list), kept
    // apart from the weekly report so the heading survives that request failing.
    if (nameFetch) {
      nameFetch().then(n => { if (n) setName(n) }).catch(() => {})
    }
  }, [studentId, nameFetch])

  // The signal report, fetched separately from the Promise.all above -- it's
  // the heaviest query, and a failure here shouldn't blank the rest of the page.
  //
  // nameFetch is a dependency because the body reads it, but callers memoise
  // it per id, so it doesn't cause an extra fetch on toggle.
  useEffect(() => {
    let cancelled = false
    apiFetch(`/api/students/${studentId}/weekly-report`)
      .then(r => {
        if (cancelled) return
        setSignalReport(r)
        setSignalError(null)
        // With no independent nameFetch, the report is the name source --
        // seed the heading from it once it arrives.
        if (!nameFetch && r?.student_name) setName(r.student_name)
      })
      // Tracked separately from "no report" -- otherwise a failed request
      // renders identically to a genuinely quiet week.
      .catch(err => {
        if (cancelled) return
        setSignalReport(null)
        setSignalError(err.message || 'Could not load signal report')
      })
    // A stale resolve could otherwise land after navigating to another
    // student and show one student's report under another's name.
    return () => { cancelled = true }
  }, [studentId, nameFetch])

  // Separate from the weekly report: a different endpoint over a different
  // table, and neither should be able to blank the other. On failure the
  // payload's own `retrieved: false` is what the panel renders, so there is
  // nothing to catch into a second error state -- but a *thrown* request has
  // no payload at all, so it is given one rather than left null, which the
  // panel would read as "still loading" for ever.
  useEffect(() => {
    let cancelled = false
    apiFetch(`/api/students/${studentId}/signal-trend`)
      .then(r => { if (!cancelled) setTrend(r) })
      .catch(() => { if (!cancelled) setTrend({ weeks: [], retrieved: false }) })
    return () => { cancelled = true }
  }, [studentId])

  // Same shape and the same reasoning as the trend above: its own endpoint,
  // its own failure, and a thrown request is given a `retrieved: false`
  // payload rather than left null, which the panel reads as still loading.
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
      // A body is required even though every field defaults -- FastAPI 422s a
      // bodyless POST regardless. An empty object means "use server defaults".
      const res = await apiFetch(`/api/students/${studentId}/learning-strategies`, {
        method: 'POST',
        body: {},
      })
      if (!isCurrent()) return
      setStrategies(res.strategies || [])
      setStrategySource(res.source || null)
      // Absent on payloads predating the field -- null leaves the panel's
      // default claim intact.
      setStrategySignals(res.basis?.signals_retrieved ?? null)
    } catch (err) {
      if (!isCurrent()) return
      setStrategies(null)
      setStrategySource(null)
      setStrategySignals(null)
      setStrategyError(err.message || 'Could not generate strategies right now.')
    } finally {
      // Only the newest request owns the spinner, or a superseded one could
      // stop it while a generation is still running.
      if (isCurrent()) setStrategyLoading(false)
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
        // Shows an honest error rather than the zeros-filled report; for a 403
        // the message is the backend's own "You do not have access...". The
        // back link stays so a teacher who mistyped an id can still get out.
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

          {/* Only rendered once loaded, or the panels show a grid of "N/A" and
              read as "no activity" rather than "still loading". The
              read-failure notice is hidden with them -- a viewer who asked not
              to see sensor data hasn't asked to be told it failed to load. */}
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

          {/* Outside the `signalReport` gate above: the trend reads a
              different table through a different endpoint, so a weekly report
              that failed says nothing about whether the term history loaded. */}
          {showSignals && trend && <SignalTrend trend={trend} />}

          {/* Behind `showSignals` for the same reason as the panels above:
              this is EEG data, so the teacher's "Hide sensor data" switch has
              to cover it. Its own gate, not the trend's — one endpoint
              failing says nothing about the other. */}
          {showSignals && focusAccuracy && (
            <FocusAccuracy data={focusAccuracy} />
          )}

          {showStrategies && (
            <StrategyPanel
              strategies={strategies}
              source={strategySource}
              signalsRetrieved={strategySignals}
              loading={strategyLoading}
              error={strategyError}
              onGenerate={generateStrategies}
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
                            {TOPIC_ICONS[topicName] || '📘'} <span className="capitalize">{topicName.replace('_', ' ')}</span>
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
