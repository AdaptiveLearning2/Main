import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { motion } from 'framer-motion'
import { ArrowLeft, BookOpen, Target, Flame, TrendingUp } from 'lucide-react'
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
  emptyTopicText,
  nameFetch,
}) {
  const [stats, setStats]         = useState(null)
  const [sessions, setSessions]   = useState([])
  const [perf, setPerf]           = useState([])
  const [loading, setLoading]     = useState(true)
  const [name, setName]           = useState(initialName)
  const [signalReport, setSignalReport] = useState(null)
  const [signalError, setSignalError]   = useState(null)

  useEffect(() => {
    Promise.all([
      apiFetch(`/api/stats/student/${studentId}`),
      apiFetch(`/api/sessions/student/${studentId}`),
      apiFetch(`/api/performance/student/${studentId}`),
    ]).then(([s, sess, p]) => {
      setStats(s)
      setSessions(sess || [])
      setPerf(p || [])
      setLoading(false)
    }).catch(() => setLoading(false))

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
              <LiveSignalSummary report={signalReport} title="Latest Signal Snapshot" />
              <WeeklySignalReport report={signalReport} title="Weekly EEG & Face Report" />
            </div>
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
