import { useEffect, useState } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { Link } from 'react-router-dom'
import { supabase } from '../../lib/supabase'
import { Users, Search, ChevronDown, Flame, Brain, Target, TrendingUp, Zap, BarChart3, FileText } from 'lucide-react'

function average(rows, key) {
  const values = (rows || [])
    .map(row => Number(row?.[key]))
    .filter(value => Number.isFinite(value))

  if (!values.length) return null
  return Math.round((values.reduce((sum, value) => sum + value, 0) / values.length) * 100) / 100
}

function percent(correct, attempted) {
  const total = Number(attempted || 0)
  if (!total) return null
  return Math.round((Number(correct || 0) / total) * 100)
}

export default function Students() {
  const [students, setStudents] = useState([])
  const [loading, setLoading] = useState(true)
  const [search, setSearch] = useState('')
  const [expandedId, setExpandedId] = useState(null)
  const [statsCache, setStatsCache] = useState({})
  const [statsLoading, setStatsLoading] = useState({})

  useEffect(() => {
    let cancelled = false

    async function loadStudents() {
      const { data: { user }, error: userError } = await supabase.auth.getUser()
      if (userError || !user) {
        if (!cancelled) setLoading(false)
        return
      }

      const { data, error } = await supabase
        .from('class_memberships')
        .select('student_id, profiles!inner(*), classes!inner(teacher_id)')
        .eq('classes.teacher_id', user.id)

      if (cancelled) return

      if (error) {
        console.error('Failed to load students:', error)
        setLoading(false)
        return
      }

      const seen = new Map()
      for (const row of data || []) {
        if (row.profiles && !seen.has(row.student_id)) {
          seen.set(row.student_id, row.profiles)
        }
      }

      setStudents(Array.from(seen.values()))
      setLoading(false)
    }

    loadStudents()
    return () => { cancelled = true }
  }, [])

  const filtered = students.filter(s => {
    const query = search.toLowerCase()
    return (s.email || s.username || s.name || s.id || '').toLowerCase().includes(query)
  })

  async function toggleExpand(studentId) {
    if (expandedId === studentId) {
      setExpandedId(null)
      return
    }

    setExpandedId(studentId)
    if (statsCache[studentId] || statsLoading[studentId]) return

    setStatsLoading(prev => ({ ...prev, [studentId]: true }))
    const stats = await getStudentStats(studentId)
    setStatsCache(prev => ({ ...prev, [studentId]: stats }))
    setStatsLoading(prev => ({ ...prev, [studentId]: false }))
  }

  async function getStudentStats(studentId) {
    const [statsRes, signalsRes, performanceRes] = await Promise.all([
      supabase.from('user_stats').select('*').eq('user_id', studentId).maybeSingle(),
      supabase.from('cognitive_signals')
        .select('focus, stress, engagement, ts')
        .eq('user_id', studentId)
        .order('ts', { ascending: false })
        .limit(200),
      supabase.from('user_math_performance')
        .select('topic_id, correct_questions, attempted_questions, math_topics(topic_name)')
        .eq('user_id', studentId)
        .limit(50),
    ])

    if (statsRes.error) console.error('Failed to load user_stats:', statsRes.error)
    if (signalsRes.error) console.error('Failed to load cognitive_signals:', signalsRes.error)
    if (performanceRes.error) console.error('Failed to load topic performance:', performanceRes.error)

    const userStats = statsRes.data
    const signals = signalsRes.data || []
    const topics = (performanceRes.data || [])
      .filter(row => Number(row.attempted_questions || 0) > 0)
      .map(row => ({
        topicId: row.topic_id,
        topicName: row.math_topics?.topic_name || row.topic_id || 'Topic',
        attempted: Number(row.attempted_questions || 0),
        correct: Number(row.correct_questions || 0),
        accuracy: percent(row.correct_questions, row.attempted_questions),
      }))
      .sort((a, b) => (a.accuracy ?? 0) - (b.accuracy ?? 0))

    const totalAccuracy = userStats && Number(userStats.total_questions || 0) > 0
      ? Math.round((Number(userStats.total_correct || 0) / Number(userStats.total_questions || 1)) * 100)
      : null

    return {
      totalAccuracy,
      totalQuestions: userStats?.total_questions ?? 0,
      currentStreak: userStats?.current_streak ?? 0,
      bestStreak: userStats?.best_streak ?? 0,
      focusScore: average(signals, 'focus'),
      stressLevel: average(signals, 'stress'),
      engagement: average(signals, 'engagement'),
      signalCount: signals.length,
      topics,
    }
  }

  return (
    <div className="p-6 lg:p-8 pb-12">
      <motion.div initial={{ opacity: 0, y: -10 }} animate={{ opacity: 1, y: 0 }} className="mb-6">
        <h1 className="text-3xl font-black text-gray-900 dark:text-white flex items-center gap-3">
          <Users className="text-violet-600" size={28} /> Students
        </h1>
        <p className="text-gray-500 dark:text-gray-400 mt-1">All students connected to your classes.</p>
      </motion.div>

      <div className="relative mb-6 max-w-sm">
        <Search size={16} className="absolute left-3.5 top-1/2 -translate-y-1/2 text-gray-400" />
        <input
          value={search}
          onChange={e => setSearch(e.target.value)}
          className="w-full pl-10 pr-4 py-2.5 bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-700 rounded-xl text-sm dark:text-white outline-none focus:ring-2 focus:ring-violet-500 transition"
          placeholder="Search students..."
        />
      </div>

      {loading ? (
        <div className="space-y-3">{[1, 2, 3, 4, 5].map(i => <div key={i} className="h-16 bg-white dark:bg-gray-900 rounded-2xl border border-gray-100 dark:border-gray-800 animate-pulse" />)}</div>
      ) : filtered.length === 0 ? (
        <div className="text-center py-16">
          <div className="text-6xl mb-4">🎓</div>
          <h3 className="text-xl font-black text-gray-900 dark:text-white mb-2">
            {students.length === 0 ? 'No students yet' : 'No results'}
          </h3>
          <p className="text-gray-500 dark:text-gray-400 text-sm">
            {students.length === 0
              ? 'Students will appear here once they join one of your classes.'
              : 'Try a different search term.'}
          </p>
        </div>
      ) : (
        <div className="bg-white dark:bg-gray-900 rounded-2xl border border-gray-100 dark:border-gray-800 shadow-sm overflow-hidden">
          <div className="grid grid-cols-4 px-5 py-3 border-b border-gray-50 dark:border-gray-800">
            <span className="text-xs font-bold uppercase tracking-widest text-gray-400 col-span-2">Student</span>
            <span className="text-xs font-bold uppercase tracking-widest text-gray-400">Joined</span>
            <span className="text-xs font-bold uppercase tracking-widest text-gray-400 text-right">Actions</span>
          </div>

          {filtered.map((s, i) => {
            const displayName = s.name || s.username || s.email?.split('@')[0] || s.id?.slice(0, 8) || 'Student'
            const initial = (displayName || '?')[0].toUpperCase()
            const joined = s.created_at ? new Date(s.created_at).toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' }) : '—'
            const isOpen = expandedId === s.id
            const isLoadingStats = !!statsLoading[s.id]
            const stats = statsCache[s.id]

            return (
              <div key={s.id} className="border-b border-gray-50 dark:border-gray-800 last:border-0">
                <motion.button
                  type="button"
                  onClick={() => toggleExpand(s.id)}
                  initial={{ opacity: 0, x: -8 }}
                  animate={{ opacity: 1, x: 0 }}
                  transition={{ delay: i * 0.03 }}
                  whileHover={{ x: 3 }}
                  className="w-full grid grid-cols-4 items-center px-5 py-4 hover:bg-slate-50 dark:hover:bg-gray-800 transition-colors text-left"
                >
                  <div className="flex items-center gap-3 col-span-2">
                    <div className="w-9 h-9 bg-gradient-to-br from-violet-400 to-purple-500 rounded-full flex items-center justify-center text-white text-xs font-black flex-shrink-0">
                      {initial}
                    </div>
                    <div>
                      <p className="text-sm font-bold text-gray-900 dark:text-white">{displayName}</p>
                      {s.email && <p className="text-xs text-gray-400">{s.email}</p>}
                    </div>
                  </div>
                  <p className="text-sm text-gray-500 dark:text-gray-400">{joined}</p>
                  <div className="flex justify-end items-center gap-3">
                    <Link
                      to={`/teacher/students/${s.id}/report`}
                      state={{ name: displayName }}
                      onClick={event => event.stopPropagation()}
                      className="hidden sm:inline-flex items-center gap-1.5 text-xs font-bold px-2.5 py-1 bg-violet-100 dark:bg-violet-900/40 text-violet-700 dark:text-violet-300 rounded-full hover:bg-violet-200 dark:hover:bg-violet-900/60 transition"
                    >
                      <FileText size={12} /> Report
                    </Link>
                    <motion.span animate={{ rotate: isOpen ? 180 : 0 }} transition={{ duration: 0.2 }}>
                      <ChevronDown size={16} className="text-gray-400" />
                    </motion.span>
                  </div>
                </motion.button>

                <AnimatePresence initial={false}>
                  {isOpen && (
                    <motion.div
                      initial={{ height: 0, opacity: 0 }}
                      animate={{ height: 'auto', opacity: 1 }}
                      exit={{ height: 0, opacity: 0 }}
                      transition={{ duration: 0.25 }}
                      className="overflow-hidden bg-slate-50 dark:bg-gray-950/40"
                    >
                      <div className="px-5 py-5">
                        {isLoadingStats ? (
                          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
                            {[1, 2, 3, 4].map(k => <div key={k} className="h-20 bg-white dark:bg-gray-900 rounded-xl border border-gray-100 dark:border-gray-800 animate-pulse" />)}
                          </div>
                        ) : !stats ? (
                          <p className="text-sm text-gray-400">Couldn&apos;t load stats for this student.</p>
                        ) : (
                          <>
                            <div className="grid grid-cols-2 sm:grid-cols-5 gap-3 mb-5">
                              <StatCard icon={<TrendingUp size={16} />} label="Total Accuracy" value={stats.totalAccuracy !== null ? `${stats.totalAccuracy}%` : '—'} sub={`${stats.totalQuestions} questions`} color="indigo" />
                              <StatCard icon={<Target size={16} />} label="Avg Focus" value={stats.focusScore !== null ? stats.focusScore : '—'} sub={stats.signalCount ? `${stats.signalCount} readings` : 'no data yet'} color="emerald" />
                              <StatCard icon={<Flame size={16} />} label="Avg Stress" value={stats.stressLevel !== null ? stats.stressLevel : '—'} sub={stats.signalCount ? `${stats.signalCount} readings` : 'no data yet'} color="rose" />
                              <StatCard icon={<Brain size={16} />} label="Engagement" value={stats.engagement !== null ? stats.engagement : '—'} sub={stats.signalCount ? 'signal average' : 'no data yet'} color="sky" />
                              <StatCard icon={<Zap size={16} />} label="Current Streak" value={stats.currentStreak} sub={`best: ${stats.bestStreak}`} color="amber" />
                            </div>

                            <div className="rounded-xl border border-gray-100 dark:border-gray-800 bg-white dark:bg-gray-900 p-4">
                              <div className="flex items-center justify-between gap-3 mb-3">
                                <h3 className="font-black text-gray-900 dark:text-white flex items-center gap-2">
                                  <BarChart3 size={16} className="text-violet-500" /> Topic Breakdown
                                </h3>
                                <Link
                                  to={`/teacher/students/${s.id}/report`}
                                  state={{ name: displayName }}
                                  className="inline-flex items-center gap-1.5 text-xs font-bold px-3 py-1.5 bg-violet-600 hover:bg-violet-700 text-white rounded-lg transition"
                                >
                                  <FileText size={12} /> Full report
                                </Link>
                              </div>

                              {stats.topics.length === 0 ? (
                                <p className="text-xs text-gray-400">No topic data yet.</p>
                              ) : (
                                <div className="space-y-3">
                                  {stats.topics.slice(0, 5).map(topic => (
                                    <div key={topic.topicId || topic.topicName}>
                                      <div className="flex items-center justify-between mb-1">
                                        <span className="text-xs font-semibold text-gray-600 dark:text-gray-300 capitalize">{String(topic.topicName).replace('_', ' ')}</span>
                                        <span className="text-xs font-black text-gray-900 dark:text-white">{topic.accuracy}%</span>
                                      </div>
                                      <div className="h-2 rounded-full bg-gray-100 dark:bg-gray-800 overflow-hidden">
                                        <div className="h-full rounded-full bg-violet-500" style={{ width: `${topic.accuracy}%` }} />
                                      </div>
                                      <p className="text-[10px] text-gray-400 mt-1">{topic.correct}/{topic.attempted} correct</p>
                                    </div>
                                  ))}
                                </div>
                              )}
                            </div>

                            {stats.totalQuestions === 0 && stats.signalCount === 0 && stats.topics.length === 0 && (
                              <p className="text-xs text-gray-400 mt-3">This student hasn&apos;t completed any sessions yet.</p>
                            )}
                          </>
                        )}
                      </div>
                    </motion.div>
                  )}
                </AnimatePresence>
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}

function StatCard({ icon, label, value, sub, color }) {
  const colorMap = {
    indigo: 'bg-indigo-100 dark:bg-indigo-900/40 text-indigo-700 dark:text-indigo-300',
    rose: 'bg-rose-100 dark:bg-rose-900/40 text-rose-700 dark:text-rose-300',
    emerald: 'bg-emerald-100 dark:bg-emerald-900/40 text-emerald-700 dark:text-emerald-300',
    amber: 'bg-amber-100 dark:bg-amber-900/40 text-amber-700 dark:text-amber-300',
    sky: 'bg-sky-100 dark:bg-sky-900/40 text-sky-700 dark:text-sky-300',
  }

  return (
    <div className="bg-white dark:bg-gray-900 rounded-xl border border-gray-100 dark:border-gray-800 p-3">
      <div className={`w-7 h-7 rounded-lg flex items-center justify-center mb-2 ${colorMap[color]}`}>
        {icon}
      </div>
      <p className="text-lg font-black text-gray-900 dark:text-white leading-none">{value}</p>
      <p className="text-[11px] text-gray-400 mt-1">{label}</p>
      {sub && <p className="text-[10px] text-gray-400 mt-0.5">{sub}</p>}
    </div>
  )
}
