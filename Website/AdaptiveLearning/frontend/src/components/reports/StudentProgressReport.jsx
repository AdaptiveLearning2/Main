import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { motion } from 'framer-motion'
import { ArrowLeft, BookOpen, Target, Flame, TrendingUp, Sparkles, ShieldCheck } from 'lucide-react'
import { WeeklySignalReport, LiveSignalSummary } from '../signals/SignalPanel'
import { apiFetch } from '../../lib/api'

const TOPIC_ICONS = { ordering:'🔢', rationals:'➗', expressions:'📐', algebra:'🔣', geometry:'📏', angle_relationships:'📐', mean:'〰️', median:'📊', mode:'🔁', probability:'🎲' }

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
 */
export default function StudentProgressReport({
  studentId,
  initialName = 'Student',
  backTo,
  backLabel,
  backHoverClass = 'hover:text-violet-600',
  emptyTopicText = 'No topic data yet.',
  nameFetch,
  showParentTools = false,
}) {
  const [stats, setStats]         = useState(null)
  const [sessions, setSessions]   = useState([])
  const [perf, setPerf]           = useState([])
  const [loading, setLoading]     = useState(true)
  const [name, setName]           = useState(initialName)
  const [signalReport, setSignalReport] = useState(null)
  const [signalError, setSignalError]   = useState(null)
  const [loadError, setLoadError]       = useState(null)
  const [includeFaceData, setIncludeFaceData] = useState(true)
  const [strategies, setStrategies] = useState([])

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
      // A failed core load (a 403 for a student outside the teacher's classes,
      // offline, or a server error) must not fall through to the zeros-filled
      // report below: that reads as a real-but-inactive student and, on the
      // teacher route, hides that the id simply isn't theirs to view. Surface it.
      setLoadError(err.message || 'Could not load this report')
      setLoading(false)
    })

    // Fetched separately from the Promise.all above: this is the newest and
    // heaviest query, and a failure here shouldn't blank the whole page when
    // the academic stats loaded fine.
    apiFetch(`/api/students/${studentId}/weekly-report`)
      .then(r => {
        setSignalReport(r)
        setSignalError(null)
        // With no independent nameFetch (the teacher case), the report is the
        // name source; seed the heading from it once it arrives.
        if (!nameFetch && r?.student_name) setName(r.student_name)
      })
      // Tracked separately from "no report": a failed request renders
      // identically to a quiet week otherwise, and telling a viewer the student
      // had no activity when the request just failed is worse than saying
      // nothing. Same distinction #16 established for ClassDetail.
      .catch(err => { setSignalReport(null); setSignalError(err.message || 'Could not load signal report') })

    // Optional independent name source (the parent's children list). Kept apart
    // from the weekly-report above so the heading still shows the real name when
    // that heavier request fails.
    if (nameFetch) {
      nameFetch().then(n => { if (n) setName(n) }).catch(() => {})
    }
  }, [studentId, nameFetch])



  function generateAtHomeStrategies() {
    const avg = signalReport?.averages || {}
    const topicRows = Array.isArray(perf) ? perf : []
    const weakestTopic = topicRows
      .filter(t => (t.attempted_questions || 0) > 0)
      .map(t => ({
        name: t.math_topics?.topic_name || t.topic_name || 'this topic',
        accuracy: Math.round(((t.correct_questions || 0) / (t.attempted_questions || 1)) * 100),
      }))
      .sort((a, b) => a.accuracy - b.accuracy)[0]

    const suggestions = []
    if (weakestTopic) {
      suggestions.push(`Spend 10–15 minutes reviewing ${weakestTopic.name.replace('_', ' ')} with 3 short practice questions before the next AI session.`)
    } else {
      suggestions.push('Start with a short mixed-topic review so the system can collect more performance data before giving topic-specific advice.')
    }

    if (avg.focus != null && Number(avg.focus) < 0.5) {
      suggestions.push('Use shorter study blocks with one clear goal, then take a 2–3 minute break before continuing.')
    } else if (avg.focus != null) {
      suggestions.push('Keep the current study routine, and add one slightly harder challenge question when focus is steady.')
    }

    if (avg.stress != null && Number(avg.stress) > 0.6) {
      suggestions.push('Begin practice with an easier warm-up question and avoid increasing difficulty immediately after a stressful session.')
    } else {
      suggestions.push('Use positive feedback after correct steps, not only after final answers, to reinforce confidence at home.')
    }

    if (includeFaceData && avg.face_attention != null && Number(avg.face_attention) < 0.5) {
      suggestions.push('Reduce distractions during practice, such as extra tabs, background video, or noisy spaces.')
    }

    setStrategies(suggestions)
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
        // A core-load failure shows an honest error here rather than the
        // zeros-filled report; for a 403 the message is the backend's own
        // "You do not have access to this student". The back link above stays,
        // so a teacher who mistyped a student id can still get out.
        <div className="rounded-2xl border border-gray-100 dark:border-gray-800 bg-white dark:bg-gray-900 p-8 shadow-sm text-center">
          <p className="text-sm font-semibold text-gray-600 dark:text-gray-300">Couldn&apos;t load this student&apos;s report.</p>
          <p className="text-xs text-gray-400 mt-1">{loadError}</p>
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
                  <p className="text-xs font-semibold uppercase tracking-widest text-gray-400 mb-1">{c.label}</p>
                  <p className="text-3xl font-black text-gray-900 dark:text-white">{c.value}</p>
                </div>
                <div className={`p-2.5 ${c.color} rounded-xl shadow-md`}>
                  <c.icon size={18} className="text-white" />
                </div>
              </motion.div>
            ))}
          </div>

          {/* Only rendered once the report loads -- the panels would otherwise
              show a full grid of "N/A" and read as "no activity" rather than
              "still loading". */}
          {signalError && (
            <div className="rounded-2xl border border-gray-100 dark:border-gray-800 bg-white dark:bg-gray-900 p-5 shadow-sm text-center">
              <p className="text-sm text-gray-500 dark:text-gray-400">Couldn&apos;t load the EEG &amp; face report.</p>
              <p className="text-xs text-gray-400 mt-1">{signalError}</p>
            </div>
          )}
          {signalReport && (
            <div className="grid lg:grid-cols-1 gap-6">
              <LiveSignalSummary report={signalReport} title="Latest Signal Snapshot" includeFace={includeFaceData} />
              <WeeklySignalReport report={signalReport} title="Weekly EEG & Face Report" includeFace={includeFaceData} />
            </div>
          )}



          {showParentTools && signalReport && (
            <motion.div initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }}
              className="rounded-2xl border border-gray-100 dark:border-gray-800 bg-white dark:bg-gray-900 p-5 shadow-sm">
              <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
                <div>
                  <h3 className="font-black text-gray-900 dark:text-white flex items-center gap-2">
                    <Sparkles size={18} className="text-emerald-500" /> At-Home Learning Strategies
                  </h3>
                  <p className="text-xs text-gray-400 mt-1">
                    Generates parent-friendly suggestions from weekly performance, EEG, and optional facial-signal data.
                  </p>
                </div>
                <label className="flex items-center gap-2 text-sm font-semibold text-gray-600 dark:text-gray-300">
                  <input
                    type="checkbox"
                    checked={includeFaceData}
                    onChange={e => { setIncludeFaceData(e.target.checked); setStrategies([]) }}
                    className="h-4 w-4 accent-emerald-600"
                  />
                  Include facial recognition data
                </label>
              </div>

              <div className="mt-4 rounded-xl bg-amber-50 dark:bg-amber-900/20 border border-amber-100 dark:border-amber-800/40 p-3 flex gap-2 text-xs text-amber-800 dark:text-amber-200">
                <ShieldCheck size={16} className="shrink-0 mt-0.5" />
                <p>EEG and facial values are learning-state indicators only. They should not be used as medical or diagnostic measurements.</p>
              </div>

              <button onClick={generateAtHomeStrategies}
                className="mt-4 px-4 py-2.5 rounded-xl bg-emerald-600 hover:bg-emerald-700 text-white text-sm font-bold transition">
                Generate At-Home Strategies
              </button>

              {strategies.length > 0 && (
                <div className="mt-4 grid gap-3">
                  {strategies.map((text, idx) => (
                    <div key={idx} className="rounded-xl bg-slate-50 dark:bg-gray-800 p-3 text-sm text-gray-700 dark:text-gray-300">
                      <span className="font-black text-emerald-600 dark:text-emerald-300">{idx + 1}. </span>{text}
                    </div>
                  ))}
                </div>
              )}
            </motion.div>
          )}

          <div className="grid lg:grid-cols-2 gap-6">
            {/* topic performance */}
            <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ delay: 0.35 }}
              className="bg-white dark:bg-gray-900 rounded-2xl border border-gray-100 dark:border-gray-800 p-5 shadow-sm">
              <h3 className="font-black text-gray-900 dark:text-white mb-5">Topic Performance</h3>
              {perf.length === 0 ? (
                <p className="text-gray-400 text-sm text-center py-6">{emptyTopicText}</p>
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

            {/* session history */}
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
  )
}
