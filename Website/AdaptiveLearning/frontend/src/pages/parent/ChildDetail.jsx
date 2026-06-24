import { useEffect, useState } from 'react'
import { useParams, Link } from 'react-router-dom'
import { motion } from 'framer-motion'
import { ArrowLeft, BookOpen, Target, Flame, TrendingUp } from 'lucide-react'
import { apiFetch } from '../../lib/api'

const TOPIC_ICONS = { ordering:'🔢', rationals:'➗', expressions:'📐', algebra:'🔣', geometry:'📏', angle_relationships:'📐', mean:'〰️', median:'📊', mode:'🔁', probability:'🎲' }

export default function ChildDetail() {
  const { id } = useParams()
  const [stats, setStats]         = useState(null)
  const [sessions, setSessions]   = useState([])
  const [perf, setPerf]           = useState([])
  const [loading, setLoading]     = useState(true)
  const [name, setName]           = useState('Child')

  useEffect(() => {
    Promise.all([
      apiFetch(`/api/stats/student/${id}`),
      apiFetch(`/api/sessions/student/${id}`),
      apiFetch(`/api/performance/student/${id}`),
    ]).then(([s, sess, p]) => {
      setStats(s)
      setSessions(sess || [])
      setPerf(p || [])
      setLoading(false)
    }).catch(() => setLoading(false))

    // also try to get name from children list
    apiFetch('/api/parent/children').then(children => {
      const child = children.find(c => c.user_id === id)
      if (child) setName(child.name)
    }).catch(() => {})
  }, [id])

  const acc = stats?.total_questions > 0 ? Math.round((stats.total_correct / stats.total_questions) * 100) : 0

  return (
    <div className="p-6 lg:p-8 pb-12">
      <motion.div initial={{ opacity: 0, y: -10 }} animate={{ opacity: 1, y: 0 }} className="mb-6">
        <Link to="/parent" className="flex items-center gap-2 text-sm text-gray-500 dark:text-gray-400 hover:text-emerald-600 mb-3 transition font-semibold w-fit">
          <ArrowLeft size={16} /> Back to Dashboard
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

          <div className="grid lg:grid-cols-2 gap-6">
            {/* topic performance */}
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