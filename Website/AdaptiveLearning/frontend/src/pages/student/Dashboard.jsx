import { useEffect, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { motion } from 'framer-motion'
import { LayoutDashboard, BookOpen, Target, TrendingUp, Flame, Brain, ArrowUpRight, Zap } from 'lucide-react'
import { apiFetch } from '../../lib/api'
import { useAuth } from '../../context/AuthContext'
import ParentRestoredBanner from '../../components/consent/ParentRestoredBanner'
import ParentLinkedBanner from '../../components/consent/ParentLinkedBanner'
import SkeletonList from '../../components/ui/Skeleton'
import StatCard from '../../components/ui/StatCard'

const TOPICS = ['ordering','rationals','expressions','algebra','geometry','angle_relationships','mean','median','mode','probability']
const ICONS  = { ordering:'🔢', rationals:'➗', expressions:'📐', algebra:'🔣', geometry:'📏', angle_relationships:'📐', mean:'〰️', median:'📊', mode:'🔁', probability:'🎲' }

// Below this many attempts, a topic's accuracy is too noisy to call it the
// student's "weakest" -- one unlucky question would read as 0%.
const MIN_ATTEMPTS_TO_RANK = 3

/** How a topic tile is tinted, from the accuracy the backend computed.
 *
 * Each tile also shows the percentage as text, not just the color, since
 * red/green alone isn't readable for everyone.
 */
function toneFor(accuracy) {
  if (accuracy >= 80) return 'bg-green-50 dark:bg-green-950/40 text-green-700 dark:text-green-300'
  if (accuracy >= 50) return 'bg-amber-50 dark:bg-amber-950/40 text-amber-700 dark:text-amber-300'
  return 'bg-rose-50 dark:bg-rose-950/40 text-rose-700 dark:text-rose-300'
}

const prettyTopic = (t) => t.replace(/_/g, ' ')

function greeting() {
  const h = new Date().getHours()
  if (h < 12) return 'Good morning'
  if (h < 17) return 'Good afternoon'
  return 'Good evening'
}

export default function StudentDashboard() {
  const { user } = useAuth()
  // Use `navigate`, not `window.location.href` -- assigning to href would
  // trigger a full page reload instead of an in-app navigation.
  const navigate = useNavigate()
  const [stats, setStats]     = useState(null)
  const [sessions, setSessions] = useState([])
  const [loading, setLoading] = useState(true)
  // The practice nudge. Stays `null` until both reads land, so a failed
  // request can't be read as "you have not practised today".
  const [nudge, setNudge] = useState(null)
  // Per-topic accuracy, keyed by `topic_name` (same slug as `TOPICS` above).
  // Defaults to `{}` on failure, which renders the same plain tile as an
  // unmeasured topic -- neither state claims anything, so neither is wrong.
  const [topics, setTopics] = useState({})

  useEffect(() => {
    Promise.all([
      // Use null, not zeros, on failure -- zeros would tell a student who
      // practised all week that they answered nothing.
      apiFetch('/api/stats/me')
        .then(s => (s?.retrieved === false ? null : s))
        .catch(() => null),
      // null (failed request) vs [] (no sessions) mean different things.
      apiFetch('/api/sessions').catch(() => null),
      apiFetch('/api/profile/me').catch(() => null),
    ]).then(([s, sess, profile]) => {
      setStats(s)
      setSessions((sess || []).slice(0, 4))
      // The browser's local day, not the school's timezone -- this nudge is
      // about the student's own afternoon.
      const today = new Date().toLocaleDateString('en-CA')   // YYYY-MM-DD, local
      const practisedToday = Array.isArray(sess) && sess.some(x =>
        x?.started_at && new Date(x.started_at).toLocaleDateString('en-CA') === today)
      setNudge(
        Array.isArray(sess) && profile
          ? { show: profile.practice_reminders !== false && !practisedToday }
          : null)
      setLoading(false)
    })
  }, [])

  // Separate effect because it depends on the student's id, unlike the other
  // fetches above.
  useEffect(() => {
    if (!user?.id) return
    let cancelled = false
    apiFetch(`/api/students/${user.id}/topic-breakdown`)
      .then(rows => {
        if (cancelled) return
        setTopics(Object.fromEntries(
          (rows || []).map(r => [r.topic_name, r])))
      })
      .catch(() => { /* tiles stay plain -- see `topics` above */ })
    return () => { cancelled = true }
  }, [user?.id])

  const acc  = stats?.total_questions > 0 ? Math.round((stats.total_correct / stats.total_questions) * 100) : 0
  const name = user?.email?.split('@')[0] || 'there'
  // `stats === null` after loading means the read failed, not that the
  // student has no record (that would be real zeros).
  const statsFailed = !loading && stats === null
  const sub = statsFailed ? "couldn't be loaded" : null

  // Show the personal best only when it beats the current streak -- while a
  // student is on their best streak, showing "best 6" under a 6 would look
  // like something's missing rather than like an achievement.
  const best = stats?.best_streak ?? 0
  const streakSub = statsFailed
    ? sub
    : (best > (stats?.current_streak ?? 0) ? `best ${best} days` : 'days')

  const CARDS = [
    { icon: BookOpen,   title: 'Questions',  value: statsFailed ? '—' : (stats?.total_questions ?? 0),  sub: sub ?? 'all time',  color: 'bg-gradient-to-br from-indigo-500 to-indigo-600',  delay: 0.1 },
    { icon: Target,     title: 'Correct',    value: statsFailed ? '—' : (stats?.total_correct ?? 0),    sub: sub ?? 'all time',  color: 'bg-gradient-to-br from-green-500 to-emerald-600',   delay: 0.2 },
    { icon: TrendingUp, title: 'Accuracy',   value: statsFailed ? '—' : `${acc}%`,                      sub: sub ?? 'overall',   color: 'bg-gradient-to-br from-violet-500 to-purple-600',   delay: 0.3 },
    { icon: Flame,      title: 'Streak',     value: statsFailed ? '—' : (stats?.current_streak ?? 0),   sub: streakSub,          color: 'bg-gradient-to-br from-orange-500 to-amber-500',    delay: 0.4 },
  ]

  // The topic the adaptive engine will actually start on. Ranked only among
  // topics with enough attempts to be meaningful (see MIN_ATTEMPTS_TO_RANK).
  const weakest = Object.values(topics)
    .filter(t => (t.attempted_questions ?? 0) >= MIN_ATTEMPTS_TO_RANK)
    .sort((a, b) => a.accuracy - b.accuracy)[0] || null

  return (
    <div className="p-6 lg:p-8 space-y-8 pb-12">
      {/* Tells the student a sensor was resumed by a parent. */}
      <ParentRestoredBanner studentId={user?.id} />

      {/* Tells the student a parent has linked their account. */}
      <ParentLinkedBanner studentId={user?.id} />

      {/* Show an explicit error instead of zeros, so a failed load doesn't
          look like "you answered nothing all term". */}
      {statsFailed && (
        <div className="rounded-2xl border border-amber-200 dark:border-amber-800 bg-amber-50 dark:bg-amber-950/40 px-5 py-3">
          <p className="text-sm font-bold text-amber-900 dark:text-amber-200">
            Your progress couldn&apos;t be loaded just now. Your work is saved — try refreshing.
          </p>
        </div>
      )}

      {/* Only rendered once both reads succeed and the student hasn't
          practised today -- a nudge from a failed request would wrongly
          tell a child they skipped a day. */}
      {nudge?.show && (
        <motion.div initial={{ opacity: 0, y: -8 }} animate={{ opacity: 1, y: 0 }}
          className="bg-indigo-50 dark:bg-indigo-950/40 border border-indigo-200 dark:border-indigo-800 rounded-2xl px-5 py-4 flex flex-wrap items-center gap-3">
          <span className="text-xl">👋</span>
          <p className="text-sm font-bold text-indigo-900 dark:text-indigo-200 flex-1 min-w-[12rem]">
            You have not practised today. A few questions is plenty.
          </p>
          <button onClick={() => navigate('/adaptive')}
            className="px-4 py-2 rounded-xl text-sm font-bold bg-indigo-600 hover:bg-indigo-700 text-white shadow transition">
            Start a session
          </button>
        </motion.div>
      )}

      {/* header */}
      <motion.div initial={{ opacity: 0, y: -12 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.35 }}
        className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-3xl font-black text-gray-900 dark:text-white">
            {greeting()}, <span className="text-indigo-600">{name}</span> 👋
          </h1>
          <p className="text-gray-500 dark:text-gray-400 mt-1">Here's your learning overview.</p>
        </div>
        <motion.div
          whileHover={{ scale: 1.05 }} whileTap={{ scale: 0.97 }}
          onClick={() => navigate('/adaptive')}
          // Wraps under the greeting on a narrow screen rather than being hidden,
          // since a phone is likely to be a student's main device.
          className="flex items-center gap-2 bg-gradient-to-r from-indigo-600 to-violet-600 text-white px-5 py-2.5 rounded-full text-sm font-bold shadow-lg cursor-pointer shrink-0"
        >
          <Brain size={16} /> Start AI Session
        </motion.div>
      </motion.div>

      {/* stat cards */}
      <div className="grid grid-cols-2 xl:grid-cols-4 gap-4">
        {CARDS.map(c => <StatCard key={c.title} {...c} />)}
      </div>

      <div className="grid xl:grid-cols-3 gap-6">
        <div className="xl:col-span-2 space-y-4">
          {/* hero banner */}
          <motion.div
            initial={{ opacity: 0, scale: 0.97 }} animate={{ opacity: 1, scale: 1 }} transition={{ delay: 0.45 }}
            whileHover={{ scale: 1.005 }}
            className="relative bg-gradient-to-br from-indigo-600 via-indigo-700 to-violet-800 rounded-2xl p-7 text-white overflow-hidden shadow-xl shadow-indigo-200 dark:shadow-indigo-950 cursor-pointer"
            onClick={() => navigate('/adaptive')}
          >
            <div className="absolute top-0 right-0 w-48 h-48 bg-white/10 rounded-full -translate-y-16 translate-x-16" />
            <div className="absolute bottom-0 left-0 w-32 h-32 bg-white/5 rounded-full translate-y-10 -translate-x-10" />
            <div className="relative">
              <div className="flex items-center gap-2 mb-3">
                <motion.div animate={{ rotate: [0, 360] }} transition={{ duration: 3, repeat: Infinity, ease: 'linear' }}>
                  <Zap size={16} className="text-yellow-300" />
                </motion.div>
                <span className="text-indigo-200 text-xs font-bold uppercase tracking-widest">AI-Powered</span>
              </div>
              <h2 className="text-2xl font-black mb-2">Start Adaptive Practice</h2>
              <p className="text-indigo-100 text-sm mb-5 max-w-sm">
                The AI reads your performance, picks your weakest topic, sets the right difficulty, and generates a custom question.
              </p>
              <div className="flex flex-wrap gap-3">
                <div className="flex items-center gap-2 bg-white text-indigo-700 px-5 py-2.5 rounded-xl font-bold text-sm shadow">
                  <Brain size={16} /> AI Adaptive <ArrowUpRight size={14} />
                </div>
                <Link to="/practice" onClick={e => e.stopPropagation()} className="flex items-center gap-2 bg-indigo-800/60 text-white px-5 py-2.5 rounded-xl font-bold text-sm hover:bg-indigo-800 transition">
                  <Target size={16} /> Classic Practice
                </Link>
              </div>
            </div>
          </motion.div>

          {/* topics grid */}
          <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ delay: 0.55 }}
            className="bg-white dark:bg-gray-900 rounded-2xl border border-gray-100 dark:border-gray-800 p-5 shadow-sm">
            <h3 className="font-black text-gray-900 dark:text-white mb-4 flex items-center gap-2">
              <BookOpen size={16} className="text-indigo-600" /> Topics in the Curriculum
            </h3>
            <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-2">
              {TOPICS.map((t, i) => {
                const row = topics[t]
                const attempted = row?.attempted_questions ?? 0
                // An unattempted topic and a failed read look the same here --
                // both draw a plain, untinted tile.
                const measured = attempted > 0
                return (
                  <motion.div key={t}
                    initial={{ opacity: 0, scale: 0.8 }} animate={{ opacity: 1, scale: 1 }}
                    transition={{ delay: 0.55 + i * 0.03 }}
                    whileHover={{ scale: 1.06 }}
                    className={`flex flex-col items-center p-3 rounded-xl text-center gap-1 ${
                      measured ? toneFor(row.accuracy) : 'bg-slate-50 dark:bg-gray-800'}`}
                  >
                    <span className="text-xl">{ICONS[t]}</span>
                    <span className={`text-xs font-semibold capitalize leading-tight ${
                      measured ? '' : 'text-gray-600 dark:text-gray-400'}`}>
                      {prettyTopic(t)}
                    </span>
                    {/* The number, not just the tint. See `toneFor`. */}
                    {measured && (
                      <span className="text-[11px] font-black tabular-nums">
                        {row.accuracy}%
                      </span>
                    )}
                  </motion.div>
                )
              })}
            </div>

            {/* Shows the topic `/adaptive` will actually open on. Hidden until
                a topic has enough attempts to rank, so this never guesses. */}
            {weakest && (
              <button
                onClick={() => navigate('/adaptive')}
                className="mt-4 w-full flex items-center justify-between gap-3 px-4 py-3 rounded-xl bg-indigo-50 dark:bg-indigo-950/40 border border-indigo-100 dark:border-indigo-900 hover:bg-indigo-100 dark:hover:bg-indigo-950/70 transition text-left"
              >
                <span className="text-sm font-bold text-indigo-900 dark:text-indigo-200">
                  Weakest so far: <span className="capitalize">{prettyTopic(weakest.topic_name)}</span>
                  <span className="font-semibold text-indigo-700 dark:text-indigo-300"> · {weakest.accuracy}%</span>
                </span>
                <span className="flex items-center gap-1 text-xs font-black text-indigo-700 dark:text-indigo-300 whitespace-nowrap">
                  Practise it <ArrowUpRight size={14} />
                </span>
              </button>
            )}
          </motion.div>
        </div>

        {/* right col */}
        <div className="space-y-4">
          {/* accuracy ring */}
          <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ delay: 0.5 }}
            className="bg-white dark:bg-gray-900 rounded-2xl border border-gray-100 dark:border-gray-800 p-5 shadow-sm text-center">
            <h3 className="font-black text-gray-900 dark:text-white mb-4">Overall Accuracy</h3>
            <div className="relative inline-flex items-center justify-center w-28 h-28 mb-3">
              <svg className="w-full h-full -rotate-90" viewBox="0 0 36 36">
                <circle cx="18" cy="18" r="15.9" fill="none" stroke="#e5e7eb" strokeWidth="3" className="dark:stroke-gray-700" />
                <motion.circle
                  cx="18" cy="18" r="15.9" fill="none"
                  stroke="url(#grad1)" strokeWidth="3" strokeLinecap="round"
                  strokeDasharray="100 100"
                  initial={{ strokeDashoffset: 100 }}
                  animate={{ strokeDashoffset: 100 - acc }}
                  transition={{ duration: 1.2, delay: 0.6, ease: 'easeOut' }}
                />
                <defs>
                  <linearGradient id="grad1" x1="0%" y1="0%" x2="100%" y2="0%">
                    <stop offset="0%" stopColor="#6366f1" />
                    <stop offset="100%" stopColor="#8b5cf6" />
                  </linearGradient>
                </defs>
              </svg>
              <div className="absolute inset-0 flex items-center justify-center">
                <p className="text-2xl font-black text-gray-900 dark:text-white">{acc}%</p>
              </div>
            </div>
            <p className="text-xs text-gray-500 dark:text-gray-400">
              {acc >= 80 ? '🔥 Crushing it!' : acc >= 50 ? '📈 Solid progress!' : '💪 Keep grinding!'}
            </p>
          </motion.div>

          {/* recent sessions */}
          <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ delay: 0.6 }}
            className="bg-white dark:bg-gray-900 rounded-2xl border border-gray-100 dark:border-gray-800 p-5 shadow-sm">
            <div className="flex items-center justify-between mb-4">
              <h3 className="font-black text-gray-900 dark:text-white">Recent Sessions</h3>
              <Link to="/history" className="text-xs text-indigo-600 dark:text-indigo-400 font-bold hover:underline flex items-center gap-0.5">
                All <ArrowUpRight size={12} />
              </Link>
            </div>
            {loading ? (
              <SkeletonList count={3} height="h-12" gap="space-y-2" />
            ) : sessions.length === 0 ? (
              <div className="text-center py-6">
                <p className="text-4xl mb-2">🏁</p>
                <p className="text-sm text-gray-500 dark:text-gray-400">No sessions yet.</p>
                <Link to="/practice" className="text-xs text-indigo-600 font-bold mt-1 inline-block">Start your first →</Link>
              </div>
            ) : (
              <div className="space-y-2">
                {sessions.map((s, i) => {
                  const pct = s.questions_answered > 0 ? Math.round((s.correct_answers / s.questions_answered) * 100) : 0
                  return (
                    <motion.div key={s.id}
                      initial={{ opacity: 0, x: -10 }} animate={{ opacity: 1, x: 0 }}
                      transition={{ delay: 0.65 + i * 0.05 }}
                      whileHover={{ x: 3 }}
                      className="flex items-center justify-between p-3 bg-slate-50 dark:bg-gray-800 rounded-xl"
                    >
                      <div>
                        <p className="text-sm font-semibold text-gray-900 dark:text-white">{s.title || 'Session'}</p>
                        <p className="text-xs text-gray-400">{new Date(s.started_at).toLocaleDateString()}</p>
                      </div>
                      <div className="text-right">
                        <p className={`text-sm font-black ${pct >= 70 ? 'text-green-500' : pct >= 40 ? 'text-amber-500' : 'text-rose-500'}`}>{pct}%</p>
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
    </div>
  )
}