import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { motion } from 'framer-motion'
import { LayoutDashboard, BookOpen, Target, TrendingUp, Flame, Brain, ArrowUpRight, Zap } from 'lucide-react'
import { apiFetch } from '../../lib/api'
import { useAuth } from '../../context/AuthContext'

const TOPICS = ['ordering','rationals','expressions','algebra','geometry','angle_relationships','mean','median','mode','probability']
const ICONS  = { ordering:'🔢', rationals:'➗', expressions:'📐', algebra:'🔣', geometry:'📏', angle_relationships:'📐', mean:'〰️', median:'📊', mode:'🔁', probability:'🎲' }

function greeting() {
  const h = new Date().getHours()
  if (h < 12) return 'Good morning'
  if (h < 17) return 'Good afternoon'
  return 'Good evening'
}

function StatCard({ icon: Icon, title, value, sub, color, delay }) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 20 }} animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.4, delay }}
      whileHover={{ y: -4, transition: { duration: 0.15 } }}
      className="relative group bg-white dark:bg-gray-900 rounded-2xl p-5 border border-gray-100 dark:border-gray-800 shadow-sm hover:shadow-lg transition-shadow"
    >
      <div className="absolute inset-0 bg-gradient-to-br from-indigo-400/10 to-violet-500/10 rounded-2xl opacity-0 group-hover:opacity-100 transition-opacity duration-300" />
      <div className="relative flex items-start justify-between">
        <div>
          <p className="text-xs font-semibold uppercase tracking-widest text-gray-400 dark:text-gray-500 mb-1">{title}</p>
          <p className="text-3xl font-black text-gray-900 dark:text-white">{value ?? '—'}</p>
          {sub && <p className="text-xs text-gray-400 dark:text-gray-500 mt-0.5">{sub}</p>}
        </div>
        <div className={`p-2.5 ${color} rounded-xl shadow-md`}>
          <Icon size={20} className="text-white" />
        </div>
      </div>
    </motion.div>
  )
}

export default function StudentDashboard() {
  const { user } = useAuth()
  const [stats, setStats]     = useState(null)
  const [sessions, setSessions] = useState([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    Promise.all([
      apiFetch('/api/stats/me').catch(() => ({ total_questions: 0, total_correct: 0, current_streak: 0, best_streak: 0 })),
      apiFetch('/api/sessions').catch(() => [])
    ]).then(([s, sess]) => {
      setStats(s)
      setSessions((sess || []).slice(0, 4))
      setLoading(false)
    })
  }, [])

  const acc  = stats?.total_questions > 0 ? Math.round((stats.total_correct / stats.total_questions) * 100) : 0
  const name = user?.email?.split('@')[0] || 'there'

  const CARDS = [
    { icon: BookOpen,   title: 'Questions',  value: stats?.total_questions ?? 0,  sub: 'all time',  color: 'bg-gradient-to-br from-indigo-500 to-indigo-600',  delay: 0.1 },
    { icon: Target,     title: 'Correct',    value: stats?.total_correct ?? 0,    sub: 'all time',  color: 'bg-gradient-to-br from-green-500 to-emerald-600',   delay: 0.2 },
    { icon: TrendingUp, title: 'Accuracy',   value: `${acc}%`,                    sub: 'overall',   color: 'bg-gradient-to-br from-violet-500 to-purple-600',   delay: 0.3 },
    { icon: Flame,      title: 'Streak',     value: stats?.current_streak ?? 0,   sub: 'days',      color: 'bg-gradient-to-br from-orange-500 to-amber-500',    delay: 0.4 },
  ]

  return (
    <div className="p-6 lg:p-8 space-y-8 pb-12">
      {/* header */}
      <motion.div initial={{ opacity: 0, y: -12 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.35 }}
        className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-black text-gray-900 dark:text-white">
            {greeting()}, <span className="text-indigo-600">{name}</span> 👋
          </h1>
          <p className="text-gray-500 dark:text-gray-400 mt-1">Here's your learning overview.</p>
        </div>
        <motion.div
          whileHover={{ scale: 1.05 }} whileTap={{ scale: 0.97 }}
          onClick={() => window.location.href = '/adaptive'}
          className="hidden md:flex items-center gap-2 bg-gradient-to-r from-indigo-600 to-violet-600 text-white px-5 py-2.5 rounded-full text-sm font-bold shadow-lg cursor-pointer"
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
            onClick={() => window.location.href = '/adaptive'}
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
              {TOPICS.map((t, i) => (
                <motion.div key={t}
                  initial={{ opacity: 0, scale: 0.8 }} animate={{ opacity: 1, scale: 1 }}
                  transition={{ delay: 0.55 + i * 0.03 }}
                  whileHover={{ scale: 1.06 }}
                  className="flex flex-col items-center p-3 bg-slate-50 dark:bg-gray-800 rounded-xl text-center gap-1"
                >
                  <span className="text-xl">{ICONS[t]}</span>
                  <span className="text-xs font-semibold text-gray-600 dark:text-gray-400 capitalize leading-tight">{t.replace('_', ' ')}</span>
                </motion.div>
              ))}
            </div>
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
              <div className="space-y-2">{[1,2,3].map(i => <div key={i} className="h-12 bg-gray-100 dark:bg-gray-800 rounded-xl animate-pulse" />)}</div>
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