import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { motion } from 'framer-motion'
import { Users, HelpCircle, BarChart3, ArrowUpRight, Brain, Zap } from 'lucide-react'
import { apiFetch } from '../../lib/api'
import { useAuth } from '../../context/AuthContext'

function StatCard({ icon: Icon, title, value, sub, color, delay }) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 20 }} animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.4, delay }}
      whileHover={{ y: -4, transition: { duration: 0.15 } }}
      className="relative group bg-white dark:bg-gray-900 rounded-2xl p-5 border border-gray-100 dark:border-gray-800 shadow-sm hover:shadow-lg transition-shadow"
    >
      <div className="absolute inset-0 bg-gradient-to-br from-violet-400/10 to-purple-500/10 rounded-2xl opacity-0 group-hover:opacity-100 transition-opacity duration-300" />
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

export default function TeacherDashboard() {
  const { user } = useAuth()
  const [questions, setQuestions] = useState([])
  const [loading, setLoading]     = useState(true)
  const name = user?.email?.split('@')[0] || 'there'

  useEffect(() => {
    apiFetch('/api/questions?limit=1000')
      .then(q => { setQuestions(q || []); setLoading(false) })
      .catch(() => setLoading(false))
  }, [])

  const easy   = questions.filter(q => q.difficulty === 'easy').length
  const medium = questions.filter(q => q.difficulty === 'medium').length
  const hard   = questions.filter(q => q.difficulty === 'hard').length

  const CARDS = [
    { icon: HelpCircle, title: 'Total Questions', value: loading ? '...' : questions.length, sub: 'in question bank',    color: 'bg-gradient-to-br from-violet-500 to-purple-600',  delay: 0.1 },
    { icon: BarChart3,  title: 'Easy',            value: loading ? '...' : easy,             sub: 'easy questions',      color: 'bg-gradient-to-br from-green-500 to-emerald-600',   delay: 0.2 },
    { icon: BarChart3,  title: 'Medium',           value: loading ? '...' : medium,           sub: 'medium questions',    color: 'bg-gradient-to-br from-amber-500 to-orange-500',    delay: 0.3 },
    { icon: BarChart3,  title: 'Hard',             value: loading ? '...' : hard,             sub: 'hard questions',      color: 'bg-gradient-to-br from-rose-500 to-red-600',        delay: 0.4 },
  ]

  const recentQuestions = questions.slice(0, 5)

  return (
    <div className="p-6 lg:p-8 space-y-8 pb-12">
      {/* header */}
      <motion.div initial={{ opacity: 0, y: -12 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.35 }}
        className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-black text-gray-900 dark:text-white">
            Hey, <span className="text-violet-600">{name}</span> 👋
          </h1>
          <p className="text-gray-500 dark:text-gray-400 mt-1">Here's what's going on with your class.</p>
        </div>
        <Link to="/teacher/questions">
          <motion.div whileHover={{ scale: 1.05 }} whileTap={{ scale: 0.97 }}
            className="hidden md:flex items-center gap-2 bg-gradient-to-r from-violet-600 to-purple-600 text-white px-5 py-2.5 rounded-full text-sm font-bold shadow-lg cursor-pointer">
            <HelpCircle size={16} /> Manage Questions
          </motion.div>
        </Link>
      </motion.div>

      {/* stat cards */}
      <div className="grid grid-cols-2 xl:grid-cols-4 gap-4">
        {CARDS.map(c => <StatCard key={c.title} {...c} />)}
      </div>

      <div className="grid xl:grid-cols-3 gap-6">
        <div className="xl:col-span-2 space-y-4">
          {/* hero banner */}
          <motion.div initial={{ opacity: 0, scale: 0.97 }} animate={{ opacity: 1, scale: 1 }} transition={{ delay: 0.45 }}
            className="relative bg-gradient-to-br from-violet-600 via-violet-700 to-purple-800 rounded-2xl p-7 text-white overflow-hidden shadow-xl shadow-violet-200 dark:shadow-violet-950">
            <div className="absolute top-0 right-0 w-48 h-48 bg-white/10 rounded-full -translate-y-16 translate-x-16" />
            <div className="absolute bottom-0 left-0 w-32 h-32 bg-white/5 rounded-full translate-y-10 -translate-x-10" />
            <div className="relative">
              <div className="flex items-center gap-2 mb-3">
                <motion.div animate={{ rotate: [0, 360] }} transition={{ duration: 3, repeat: Infinity, ease: 'linear' }}>
                  <Zap size={16} className="text-yellow-300" />
                </motion.div>
                <span className="text-violet-200 text-xs font-bold uppercase tracking-widest">Teacher Tools</span>
              </div>
              <h2 className="text-2xl font-black mb-2">Manage Your Class</h2>
              <p className="text-violet-100 text-sm mb-5 max-w-sm">
                View student performance, manage the question bank, and track class-wide analytics — all in one place.
              </p>
              <div className="flex flex-wrap gap-3">
                <Link to="/teacher/students" className="flex items-center gap-2 bg-white text-violet-700 px-5 py-2.5 rounded-xl font-bold text-sm shadow hover:bg-violet-50 transition">
                  <Users size={16} /> View Students <ArrowUpRight size={14} />
                </Link>
                <Link to="/teacher/analytics" className="flex items-center gap-2 bg-violet-800/60 text-white px-5 py-2.5 rounded-xl font-bold text-sm hover:bg-violet-800 transition">
                  <BarChart3 size={16} /> Analytics
                </Link>
              </div>
            </div>
          </motion.div>

          {/* quick actions */}
          <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ delay: 0.55 }}
            className="grid grid-cols-1 sm:grid-cols-3 gap-3">
            {[
              { to: '/teacher/students',  icon: '🎓', title: 'Students',  desc: 'View all enrolled students' },
              { to: '/teacher/questions', icon: '❓', title: 'Questions', desc: 'Manage the question bank' },
              { to: '/teacher/analytics', icon: '📊', title: 'Analytics', desc: 'Question bank breakdown' },
            ].map((a, i) => (
              <Link to={a.to} key={a.to}>
                <motion.div initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.55 + i * 0.07 }}
                  whileHover={{ y: -3 }}
                  className="bg-white dark:bg-gray-900 rounded-2xl border border-gray-100 dark:border-gray-800 p-4 shadow-sm hover:shadow-md hover:border-violet-300 dark:hover:border-violet-700 transition group">
                  <div className="text-2xl mb-2">{a.icon}</div>
                  <p className="font-black text-gray-900 dark:text-white text-sm mb-0.5">{a.title}</p>
                  <p className="text-xs text-gray-500 dark:text-gray-400">{a.desc}</p>
                  <div className="flex items-center gap-1 mt-3 text-violet-600 dark:text-violet-400 text-xs font-bold group-hover:gap-2 transition-all">
                    Go <ArrowUpRight size={12} />
                  </div>
                </motion.div>
              </Link>
            ))}
          </motion.div>
        </div>

        {/* recent questions */}
        <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ delay: 0.5 }}
          className="bg-white dark:bg-gray-900 rounded-2xl border border-gray-100 dark:border-gray-800 p-5 shadow-sm h-fit">
          <div className="flex items-center justify-between mb-4">
            <h3 className="font-black text-gray-900 dark:text-white">Recent Questions</h3>
            <Link to="/teacher/questions" className="text-xs text-violet-600 dark:text-violet-400 font-bold hover:underline flex items-center gap-0.5">
              All <ArrowUpRight size={12} />
            </Link>
          </div>
          {loading ? (
            <div className="space-y-2">{[1,2,3].map(i => <div key={i} className="h-12 bg-gray-100 dark:bg-gray-800 rounded-xl animate-pulse" />)}</div>
          ) : recentQuestions.length === 0 ? (
            <div className="text-center py-6">
              <p className="text-4xl mb-2">📭</p>
              <p className="text-sm text-gray-500 dark:text-gray-400">No questions yet.</p>
            </div>
          ) : (
            <div className="space-y-2">
              {recentQuestions.map((q, i) => (
                <motion.div key={q.id}
                  initial={{ opacity: 0, x: -8 }} animate={{ opacity: 1, x: 0 }} transition={{ delay: 0.6 + i * 0.05 }}
                  whileHover={{ x: 3 }}
                  className="p-3 bg-slate-50 dark:bg-gray-800 rounded-xl"
                >
                  <p className="text-xs font-semibold text-gray-900 dark:text-white line-clamp-2">{q.question_text}</p>
                  <div className="flex gap-2 mt-1.5">
                    {q.subject && (
                      <span className="text-[10px] font-bold px-2 py-0.5 bg-violet-100 dark:bg-violet-900/40 text-violet-700 dark:text-violet-300 rounded-full capitalize">{q.subject}</span>
                    )}
                    {q.difficulty && (
                      <span className={`text-[10px] font-bold px-2 py-0.5 rounded-full capitalize ${q.difficulty === 'easy' ? 'bg-green-100 text-green-700 dark:bg-green-900/40 dark:text-green-300' : q.difficulty === 'hard' ? 'bg-rose-100 text-rose-700 dark:bg-rose-900/40 dark:text-rose-300' : 'bg-amber-100 text-amber-700 dark:bg-amber-900/40 dark:text-amber-300'}`}>
                        {q.difficulty}
                      </span>
                    )}
                  </div>
                </motion.div>
              ))}
            </div>
          )}
        </motion.div>
      </div>
    </div>
  )
}