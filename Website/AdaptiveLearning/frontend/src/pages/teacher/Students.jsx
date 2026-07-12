import { useEffect, useState } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { Users, Search, ChevronDown, ChevronRight, Target, Brain, Zap, Eye } from 'lucide-react'
import { apiFetch } from '../../lib/api'
import { WeeklySignalReport, LiveSignalSummary } from '../../components/signals/SignalPanel'

function percent(n) {
  if (n === null || n === undefined || Number.isNaN(Number(n))) return 'N/A'
  return `${Math.round(Number(n))}%`
}

export default function Students() {
  const [students, setStudents] = useState([])
  const [loading, setLoading] = useState(true)
  const [search, setSearch] = useState('')
  const [expanded, setExpanded] = useState(null)

  useEffect(() => {
    apiFetch('/api/teacher/students')
      .then(data => setStudents(data || []))
      .catch(err => {
        console.error(err)
        setStudents([])
      })
      .finally(() => setLoading(false))
  }, [])

  const filtered = students.filter(s =>
    (s.email || s.name || s.user_id || s.class_name || '').toLowerCase().includes(search.toLowerCase())
  )

  return (
    <div className="p-6 lg:p-8 pb-12">
      <motion.div initial={{ opacity: 0, y: -10 }} animate={{ opacity: 1, y: 0 }} className="mb-6">
        <h1 className="text-3xl font-black text-gray-900 dark:text-white flex items-center gap-3">
          <Users className="text-violet-600" size={28} /> Students
        </h1>
        <p className="text-gray-500 dark:text-gray-400 mt-1">Students connected to your classes, with learning, EEG, and facial-recognition summaries.</p>
      </motion.div>

      <div className="relative mb-6 max-w-sm">
        <Search size={16} className="absolute left-3.5 top-1/2 -translate-y-1/2 text-gray-400" />
        <input value={search} onChange={e => setSearch(e.target.value)}
          className="w-full pl-10 pr-4 py-2.5 bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-700 rounded-xl text-sm dark:text-white outline-none focus:ring-2 focus:ring-violet-500 transition"
          placeholder="Search students..." />
      </div>

      {loading ? (
        <div className="space-y-3">{[1,2,3,4].map(i => <div key={i} className="h-20 bg-white dark:bg-gray-900 rounded-2xl border border-gray-100 dark:border-gray-800 animate-pulse" />)}</div>
      ) : filtered.length === 0 ? (
        <div className="text-center py-16">
          <div className="text-6xl mb-4">🎓</div>
          <h3 className="text-xl font-black text-gray-900 dark:text-white mb-2">No connected students</h3>
          <p className="text-gray-500 dark:text-gray-400 text-sm">Students will appear here after they join one of your classes.</p>
        </div>
      ) : (
        <div className="space-y-3">
          {filtered.map((s, i) => {
            const initial = (s.name || s.email || s.user_id || '?')[0].toUpperCase()
            const joined = s.joined_at ? new Date(s.joined_at).toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' }) : '—'
            const isOpen = expanded === s.user_id
            const report = s.weekly_report || {}
            return (
              <motion.div key={s.user_id}
                initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: i * 0.03 }}
                className="bg-white dark:bg-gray-900 rounded-2xl border border-gray-100 dark:border-gray-800 shadow-sm overflow-hidden">
                <button onClick={() => setExpanded(isOpen ? null : s.user_id)} className="w-full text-left p-5 hover:bg-slate-50 dark:hover:bg-gray-800 transition-colors">
                  <div className="grid lg:grid-cols-12 gap-4 items-center">
                    <div className="lg:col-span-4 flex items-center gap-3">
                      <div className="w-10 h-10 bg-gradient-to-br from-violet-400 to-purple-500 rounded-full flex items-center justify-center text-white text-xs font-black flex-shrink-0">
                        {initial}
                      </div>
                      <div>
                        <p className="text-sm font-bold text-gray-900 dark:text-white">{s.name}</p>
                        {s.email && <p className="text-xs text-gray-400">{s.email}</p>}
                        <p className="text-xs text-gray-400">{s.class_name || 'Class'} · Joined {joined}</p>
                      </div>
                    </div>
                    <div className="lg:col-span-7 grid grid-cols-2 md:grid-cols-4 gap-3">
                      <div className="rounded-xl bg-slate-50 dark:bg-gray-800 p-3">
                        <p className="text-xs text-gray-400 font-bold uppercase">Accuracy</p>
                        <p className="font-black text-gray-900 dark:text-white">{percent(s.accuracy)}</p>
                      </div>
                      <div className="rounded-xl bg-emerald-50 dark:bg-emerald-900/20 p-3">
                        <p className="text-xs text-gray-400 font-bold uppercase">Focus</p>
                        <p className="font-black text-emerald-600">{percent(report?.averages?.focus)}</p>
                      </div>
                      <div className="rounded-xl bg-rose-50 dark:bg-rose-900/20 p-3">
                        <p className="text-xs text-gray-400 font-bold uppercase">Stress</p>
                        <p className="font-black text-rose-600">{percent(report?.averages?.stress)}</p>
                      </div>
                      <div className="rounded-xl bg-sky-50 dark:bg-sky-900/20 p-3">
                        <p className="text-xs text-gray-400 font-bold uppercase">Face Attention</p>
                        <p className="font-black text-sky-600">{percent(report?.averages?.face_attention)}</p>
                      </div>
                    </div>
                    <div className="lg:col-span-1 flex justify-end text-gray-400">
                      {isOpen ? <ChevronDown size={20} /> : <ChevronRight size={20} />}
                    </div>
                  </div>
                </button>

                <AnimatePresence>
                  {isOpen && (
                    <motion.div initial={{ height: 0, opacity: 0 }} animate={{ height: 'auto', opacity: 1 }} exit={{ height: 0, opacity: 0 }} className="overflow-hidden">
                      <div className="p-5 pt-0 space-y-5 border-t border-gray-50 dark:border-gray-800">
                        <div className="grid grid-cols-2 md:grid-cols-4 gap-3 pt-5">
                          <div className="flex items-center gap-3 rounded-2xl bg-slate-50 dark:bg-gray-800 p-4"><Target className="text-violet-500" /><div><p className="text-xs text-gray-400">Questions</p><p className="font-black dark:text-white">{s.stats?.total_questions || 0}</p></div></div>
                          <div className="flex items-center gap-3 rounded-2xl bg-slate-50 dark:bg-gray-800 p-4"><Target className="text-green-500" /><div><p className="text-xs text-gray-400">Correct</p><p className="font-black dark:text-white">{s.stats?.total_correct || 0}</p></div></div>
                          <div className="flex items-center gap-3 rounded-2xl bg-slate-50 dark:bg-gray-800 p-4"><Brain className="text-emerald-500" /><div><p className="text-xs text-gray-400">Best Streak</p><p className="font-black dark:text-white">{s.stats?.best_streak || 0}</p></div></div>
                          <div className="flex items-center gap-3 rounded-2xl bg-slate-50 dark:bg-gray-800 p-4"><Zap className="text-amber-500" /><div><p className="text-xs text-gray-400">AI Sessions</p><p className="font-black dark:text-white">{report?.sample_counts?.sessions || 0}</p></div></div>
                        </div>

                        <div className="grid lg:grid-cols-2 gap-5">
                          <div className="rounded-2xl border border-gray-100 dark:border-gray-800 p-5">
                            <h3 className="font-black text-gray-900 dark:text-white mb-4">Per-Topic Score Breakdown</h3>
                            {s.topic_breakdown?.length ? (
                              <div className="space-y-3">
                                {s.topic_breakdown.map(t => (
                                  <div key={t.topic_id || t.topic_name}>
                                    <div className="flex justify-between text-sm mb-1"><span className="font-semibold text-gray-700 dark:text-gray-300 capitalize">{String(t.topic_name).replace('_', ' ')}</span><span className="font-black text-gray-900 dark:text-white">{percent(t.accuracy)}</span></div>
                                    <div className="h-2 bg-gray-100 dark:bg-gray-700 rounded-full overflow-hidden"><div className="h-full bg-violet-500 rounded-full" style={{ width: `${t.accuracy || 0}%` }} /></div>
                                    <p className="text-xs text-gray-400 mt-1">{t.correct_questions}/{t.attempted_questions} correct</p>
                                  </div>
                                ))}
                              </div>
                            ) : <p className="text-sm text-gray-400 py-6 text-center">No topic data available yet.</p>}
                          </div>

                          <LiveSignalSummary report={report} title="Latest EEG & Face Signals" />
                        </div>
                        <WeeklySignalReport report={report} />
                      </div>
                    </motion.div>
                  )}
                </AnimatePresence>
              </motion.div>
            )
          })}
        </div>
      )}
    </div>
  )
}
