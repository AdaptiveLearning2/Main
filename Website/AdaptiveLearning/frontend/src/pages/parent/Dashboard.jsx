import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { motion } from 'framer-motion'
<<<<<<< HEAD
import { Users, ArrowUpRight, TrendingUp, BookOpen, Flame, Brain, Zap, Eye, Activity } from 'lucide-react'
=======
import { Users, ArrowUpRight, TrendingUp, BookOpen, Flame, Brain, Eye, Zap } from 'lucide-react'
>>>>>>> 4fa1ce3 (Add parent reports and signal safety features)
import { apiFetch } from '../../lib/api'
import { useAuth } from '../../context/AuthContext'
import { formatSignalValue } from '../../components/signals/SignalPanel'

export default function ParentDashboard() {
  const { user } = useAuth()
  const [children, setChildren]   = useState([])
  const [loading, setLoading]     = useState(true)
  const [error, setError]         = useState(false)
  const name = user?.email?.split('@')[0] || 'there'

  useEffect(() => {
    apiFetch('/api/parent/children')
      .then(c => { setChildren(c); setLoading(false) })
      .catch(() => { setError(true); setLoading(false) })
  }, [])

  return (
    <div className="p-6 lg:p-8 pb-12 space-y-8">
      <motion.div initial={{ opacity: 0, y: -10 }} animate={{ opacity: 1, y: 0 }}>
        <h1 className="text-3xl font-black text-gray-900 dark:text-white">Hey, <span className="text-emerald-600">{name}</span> 👋</h1>
        <p className="text-gray-500 dark:text-gray-400 mt-1">Here's how your {children.length === 1 ? 'child is' : 'children are'} doing this week.</p>
      </motion.div>

      {loading ? (
        <div className="space-y-4">{[1,2].map(i => <div key={i} className="h-48 bg-white dark:bg-gray-900 rounded-2xl border border-gray-100 dark:border-gray-800 animate-pulse" />)}</div>
      ) : error ? (
        <div className="text-center py-16">
          <p className="text-4xl mb-3">⚠️</p>
          <p className="text-gray-500 dark:text-gray-400">Couldn't load data. Make sure the backend is running.</p>
        </div>
      ) : children.length === 0 ? (
        <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }}
          className="text-center py-16 bg-white dark:bg-gray-900 rounded-2xl border border-gray-100 dark:border-gray-800 shadow-sm">
          <div className="text-6xl mb-4">👦</div>
          <h3 className="text-xl font-black text-gray-900 dark:text-white mb-2">No children linked yet</h3>
          <p className="text-gray-500 dark:text-gray-400 text-sm mb-6 max-w-sm mx-auto">
            Link your child's account using their User ID. They can find it on their Profile page.
          </p>
          <Link to="/parent/link" className="inline-flex items-center gap-2 px-6 py-3 bg-emerald-600 text-white rounded-xl font-bold hover:bg-emerald-700 transition shadow">
            <Users size={16} /> Link a Child
          </Link>
        </motion.div>
      ) : (
        <div className="space-y-6">
          {children.map((child, i) => {
            const acc = child.stats?.total_questions > 0
              ? Math.round((child.stats.total_correct / child.stats.total_questions) * 100)
              : 0
            const report = child.weekly_report || {}
            const eeg = report.eeg || {}
            const face = report.face || {}
            const perf = report.performance || {}
            return (
              <motion.div key={child.user_id}
                initial={{ opacity: 0, y: 16 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: i * 0.1 }}
                className="bg-white dark:bg-gray-900 rounded-2xl border border-gray-100 dark:border-gray-800 shadow-sm overflow-hidden">

                <div className="flex items-center justify-between p-5 border-b border-gray-50 dark:border-gray-800">
                  <div className="flex items-center gap-3">
                    <div className="w-12 h-12 bg-gradient-to-br from-emerald-400 to-teal-500 rounded-xl flex items-center justify-center text-white font-black text-lg shadow">
                      {(child.name || '?')[0].toUpperCase()}
                    </div>
                    <div>
                      <h3 className="font-black text-gray-900 dark:text-white">{child.name}</h3>
                      <p className="text-xs text-gray-400">{child.email}</p>
                    </div>
                  </div>
                  <Link to={`/parent/child/${child.user_id}`}>
                    <motion.div whileHover={{ scale: 1.04 }} whileTap={{ scale: 0.97 }}
                      className="flex items-center gap-1.5 px-4 py-2 bg-emerald-50 dark:bg-emerald-900/30 text-emerald-700 dark:text-emerald-300 rounded-xl text-sm font-bold hover:bg-emerald-100 transition">
                      Full Report <ArrowUpRight size={14} />
                    </motion.div>
                  </Link>
                </div>

                <div className="grid grid-cols-2 sm:grid-cols-4 gap-0 divide-x divide-gray-50 dark:divide-gray-800">
                  {[
                    { icon: BookOpen,   label: 'Questions', value: child.stats?.total_questions ?? 0,  color: 'text-indigo-600' },
                    { icon: TrendingUp, label: 'Accuracy',  value: `${acc}%`, color: acc >= 70 ? 'text-green-600' : acc >= 40 ? 'text-amber-600' : 'text-rose-600' },
                    { icon: Flame,      label: 'Streak',    value: `${child.stats?.current_streak ?? 0}d`, color: 'text-orange-500' },
                    { icon: TrendingUp, label: 'Correct',   value: child.stats?.total_correct ?? 0, color: 'text-violet-600' },
                  ].map(s => (
                    <div key={s.label} className="p-4 text-center">
                      <s.icon size={18} className={`mx-auto mb-1 ${s.color}`} />
                      <p className="text-xl font-black text-gray-900 dark:text-white">{s.value}</p>
                      <p className="text-[11px] uppercase tracking-wider font-bold text-gray-400">{s.label}</p>
                    </div>
                  ))}
                </div>

<<<<<<< HEAD
                {/* Weekly signal averages. Rendered only when the child has
                    actually recorded signals -- an all-"N/A" row reads as
                    "something is broken" rather than "nothing recorded yet". */}
                {child.signal_summary && (child.signal_summary.sessions > 0 || child.signal_summary.focus != null) && (
                  <div className="grid grid-cols-2 lg:grid-cols-4 gap-3 p-4 border-t border-gray-50 dark:border-gray-800">
                    {[
                      { icon: Brain,    label: 'Weekly Focus',  value: child.signal_summary.focus,          color: 'text-emerald-600' },
                      { icon: Zap,      label: 'Weekly Stress', value: child.signal_summary.stress,         color: 'text-rose-600' },
                      { icon: Eye,      label: 'Face Attention', value: child.signal_summary.face_attention, color: 'text-sky-600' },
                      { icon: Activity, label: 'Sessions',      value: child.signal_summary.sessions, raw: true, color: 'text-amber-600' },
                    ].map(item => (
                      <div key={item.label} className="rounded-2xl bg-slate-50 dark:bg-gray-800 p-4">
                        <item.icon size={17} className={`${item.color} mb-2`} />
                        <p className={`text-xl font-black ${item.color}`}>
                          {item.raw
                            ? (item.value ?? 0)
                            : (item.value != null ? `${Math.round(item.value * 100)}%` : 'N/A')}
                        </p>
                        <p className="text-xs text-gray-500 dark:text-gray-400 mt-0.5">{item.label}</p>
                      </div>
                    ))}
                  </div>
                )}

                {/* recent sessions */}
=======
                <div className="grid grid-cols-2 lg:grid-cols-4 gap-3 p-4 border-t border-gray-50 dark:border-gray-800 bg-slate-50/60 dark:bg-gray-950/20">
                  <SignalCard icon={<Brain size={15} />} label="Weekly focus" value={formatSignalValue(eeg.avg_focus)} sub={eeg.sample_count ? `${eeg.sample_count} readings` : 'no EEG data'} />
                  <SignalCard icon={<Zap size={15} />} label="Weekly stress" value={formatSignalValue(eeg.avg_stress)} sub={eeg.sample_count ? 'learning indicator' : 'no EEG data'} />
                  <SignalCard icon={<Eye size={15} />} label="Face attention" value={formatSignalValue(face?.avg_attention)} sub={face?.dominant_emotion || 'no face data'} />
                  <SignalCard icon={<BookOpen size={15} />} label="AI sessions" value={perf.session_count ?? 0} sub="last 7 days" />
                </div>

>>>>>>> 4fa1ce3 (Add parent reports and signal safety features)
                {child.sessions?.length > 0 && (
                  <div className="p-4 border-t border-gray-50 dark:border-gray-800">
                    <p className="text-xs font-bold text-gray-500 dark:text-gray-400 uppercase tracking-widest mb-3">Recent Sessions</p>
                    <div className="space-y-2">
                      {child.sessions.slice(0, 3).map(s => {
                        const sAcc = s.questions_answered > 0 ? Math.round((s.correct_answers / s.questions_answered) * 100) : 0
                        return (
                          <div key={s.id} className="flex items-center justify-between p-2.5 bg-slate-50 dark:bg-gray-800 rounded-xl">
                            <div>
                              <p className="text-xs font-semibold text-gray-900 dark:text-white">{s.title || 'Practice Session'}</p>
                              <p className="text-xs text-gray-400">{new Date(s.started_at).toLocaleDateString()}</p>
                            </div>
                            <div className="text-right">
                              <p className={`text-sm font-black ${sAcc >= 70 ? 'text-green-500' : sAcc >= 40 ? 'text-amber-500' : 'text-rose-500'}`}>{sAcc}%</p>
                              <p className="text-xs text-gray-400">{s.questions_answered}q</p>
                            </div>
                          </div>
                        )
                      })}
                    </div>
                  </div>
                )}
              </motion.div>
            )
          })}

          <Link to="/parent/link" className="flex items-center justify-center gap-2 p-4 bg-white dark:bg-gray-900 rounded-2xl border-2 border-dashed border-gray-200 dark:border-gray-700 text-gray-400 hover:border-emerald-400 hover:text-emerald-600 transition font-semibold text-sm">
            <Users size={16} /> Link another child
          </Link>
        </div>
      )}
    </div>
  )
}

function SignalCard({ icon, label, value, sub }) {
  return (
    <div className="bg-white dark:bg-gray-900 rounded-xl border border-gray-100 dark:border-gray-800 p-3">
      <div className="text-emerald-600 dark:text-emerald-300 mb-2">{icon}</div>
      <p className="text-lg font-black text-gray-900 dark:text-white">{value}</p>
      <p className="text-[11px] uppercase tracking-widest font-bold text-gray-400">{label}</p>
      {sub && <p className="text-[10px] text-gray-400 mt-0.5">{sub}</p>}
    </div>
  )
}
