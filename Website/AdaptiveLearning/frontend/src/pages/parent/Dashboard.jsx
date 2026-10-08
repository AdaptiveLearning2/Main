import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { m } from 'framer-motion'
import { Users, ArrowUpRight, TrendingUp, BookOpen, Flame, Brain, Zap, Activity, Sparkles, ShieldCheck } from 'lucide-react'
import { apiFetch } from '../../lib/api'
import { useAuth } from '../../context/AuthContext'
import { usePrefetchWhenIdle } from '../../lib/prefetch'
import { pages } from '../../routes'
import ChildWithdrewBanner from '../../components/consent/ChildWithdrewBanner'
import { pct, valueOrReason, emotionOn as faceIncluded } from '../../lib/signalFormat'
import { stagger } from '../../lib/stagger'

// Only the fields the tiles below render; keep in step with them.
function hasSignalSummary(summary) {
  return Boolean(summary && (summary.sessions > 0 || summary.focus != null || summary.stress != null))
}

// A failed summary still answers 200 with defaults; absent on older payloads means read.
function signalsRetrieved(summary) {
  return summary?.retrieved !== false
}

// A default parameter must be a name: the compiler cannot build a component with an inline arrow there.
const asIs = v => v

export default function ParentDashboard() {
  const { displayName } = useAuth()
  const [children, setChildren]   = useState([])
  const [loading, setLoading]     = useState(true)
  const [error, setError]         = useState(false)
  const name = displayName || 'there'
  // A child's report is the usual next click, and it carries the chart library.
  usePrefetchWhenIdle(pages.parentChild)

  useEffect(() => {
    let cancelled = false
    apiFetch('/api/parent/children')
      .then(c => { if (!cancelled) { setChildren(c || []); setError(false); setLoading(false) } })
      .catch(() => { if (!cancelled) { setError(true); setLoading(false) } })
    return () => { cancelled = true }
  }, [])

  return (
    <div className="p-6 lg:p-8 pb-12 space-y-8">
      <ChildWithdrewBanner />

      <m.div initial={{ opacity: 0, y: -10 }} animate={{ opacity: 1, y: 0 }}>
        <h1 className="text-3xl font-black text-gray-900 dark:text-white">Hey, <span className="text-emerald-600">{name}</span> 👋</h1>
        <p className="text-gray-500 dark:text-gray-400 mt-1">Here's how your {children.length === 1 ? 'child is' : 'children are'} doing this week.</p>
      </m.div>

      {/* A failed refresh over existing rows is a banner, not a takeover. */}
      {error && children.length > 0 && (
        <div className="rounded-2xl border border-amber-200 dark:border-amber-900/50 bg-amber-50 dark:bg-amber-900/20 px-4 py-3 text-sm text-amber-800 dark:text-amber-200">
          Couldn't refresh this page just now — showing the last data loaded.
        </div>
      )}

      {loading ? (
        <div className="space-y-4">{[1,2].map(i => <div key={i} className="h-48 bg-white dark:bg-gray-900 rounded-2xl border border-gray-100 dark:border-gray-800 animate-pulse" />)}</div>
      ) : error && children.length === 0 ? (
        <div className="text-center py-16">
          <p className="text-4xl mb-3">⚠️</p>
          <p className="text-gray-500 dark:text-gray-400">Couldn't load data. Make sure the backend is running.</p>
        </div>
      ) : children.length === 0 ? (
        <m.div initial={{ opacity: 0 }} animate={{ opacity: 1 }}
          className="text-center py-16 bg-white dark:bg-gray-900 rounded-2xl border border-gray-100 dark:border-gray-800 shadow-sm">
          <div className="text-6xl mb-4">👦</div>
          <h3 className="text-xl font-black text-gray-900 dark:text-white mb-2">No children linked yet</h3>
          <p className="text-gray-500 dark:text-gray-400 text-sm mb-6 max-w-sm mx-auto">
            Link your child's account using their User ID. They can find it on their Profile page.
          </p>
          <Link to="/parent/link" className="inline-flex items-center gap-2 px-6 py-3 bg-emerald-600 text-white rounded-xl font-bold hover:bg-emerald-700 transition shadow">
            <Users size={16} /> Link a Child
          </Link>
        </m.div>
      ) : (
        <div className="space-y-6">
          {children.map((child, i) => {
            // `retrieved: false` carries placeholder zeros; no questions is no accuracy, not 0%.
            const statsRead = child.stats?.retrieved !== false
            const acc = statsRead && child.stats?.total_questions > 0
              ? Math.round((child.stats.total_correct / child.stats.total_questions) * 100)
              : null
            const stat = (value, format = asIs) => (statsRead ? format(value ?? 0) : '—')
            const signals = child.signal_summary || {}
            // The EEG channel's reason for a missing figure, from the fields the summary carries.
            const eeg = { on: signals.eeg_enabled !== false, revokedAt: signals.eeg_revoked_at,
                          consentRetrieved: signals.consent_retrieved,
                          samples: signals.cognitive_samples, erasedAt: signals.eeg_erased_at }
            const retrieved = signalsRetrieved(signals)
            const showSignals = retrieved && hasSignalSummary(signals)
            const initial = (child.name || child.email || '?')[0].toUpperCase()
            return (
              <m.div key={child.user_id}
                initial={{ opacity: 0, y: 16 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: stagger(i, 0.1) }}
                className="bg-white dark:bg-gray-900 rounded-2xl border border-gray-100 dark:border-gray-800 shadow-sm overflow-hidden">

                <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between p-5 border-b border-gray-50 dark:border-gray-800">
                  <div className="flex items-center gap-3">
                    <div className="w-12 h-12 bg-gradient-to-br from-emerald-400 to-teal-500 rounded-xl flex items-center justify-center text-white font-black text-lg shadow">
                      {initial}
                    </div>
                    <div>
                      <h3 className="font-black text-gray-900 dark:text-white">{child.name || 'Student'}</h3>
                      <p className="text-xs text-gray-600 dark:text-gray-400">{child.email || 'No email available'}</p>
                    </div>
                  </div>
                  <Link to={`/parent/child/${child.user_id}`} state={{ name: child.name || null }}>
                    <m.div whileHover={{ scale: 1.04 }} whileTap={{ scale: 0.97 }}
                      className="flex items-center justify-center gap-1.5 px-4 py-2 bg-emerald-50 dark:bg-emerald-900/30 text-emerald-700 dark:text-emerald-300 rounded-xl text-sm font-bold hover:bg-emerald-100 transition">
                      Full Report <ArrowUpRight size={14} />
                    </m.div>
                  </Link>
                </div>

                <div className="grid grid-cols-2 sm:grid-cols-4 gap-0 divide-x divide-gray-50 dark:divide-gray-800">
                  {[
                    { icon: BookOpen,   label: 'Questions', value: stat(child.stats?.total_questions), color: 'text-indigo-600' },
                    { icon: TrendingUp, label: 'Accuracy',  value: acc === null ? '—' : `${acc}%`,
                      color: acc === null ? 'text-gray-600 dark:text-gray-400' : acc >= 70 ? 'text-green-600' : acc >= 40 ? 'text-amber-600' : 'text-rose-600' },
                    { icon: Flame,      label: 'Streak',    value: stat(child.stats?.current_streak, v => `${v}d`), color: 'text-orange-500' },
                    { icon: TrendingUp, label: 'Correct',   value: stat(child.stats?.total_correct), color: 'text-violet-600' },
                  ].map(s => (
                    <div key={s.label} className="p-4 text-center">
                      <s.icon size={18} className={`mx-auto mb-1 ${s.color}`} />
                      <p className={`text-2xl font-black ${s.color}`}>{s.value}</p>
                      <p className="text-xs text-gray-500 dark:text-gray-400 mt-0.5">{s.label}</p>
                    </div>
                  ))}
                </div>
                {!statsRead && (
                  <p className="px-5 pb-3 text-xs text-gray-600 dark:text-gray-400">
                    These practice totals could not be loaded.
                  </p>
                )}

                {showSignals ? (
                  <div className="grid grid-cols-2 lg:grid-cols-4 gap-3 p-4 border-t border-gray-50 dark:border-gray-800 bg-slate-50/60 dark:bg-gray-950/20">
                    {[
                      { icon: Brain,    label: 'Weekly Focus',   value: valueOrReason(pct(signals.focus), eeg),  color: 'text-emerald-600' },
                      { icon: Zap,      label: 'Weekly Stress',  value: valueOrReason(pct(signals.stress), eeg), color: 'text-rose-600' },
                      { icon: Activity, label: 'AI Sessions',    value: signals.sessions ?? 0,           color: 'text-amber-600' },
                    ].map(item => (
                      <div key={item.label} className="rounded-2xl bg-white dark:bg-gray-900 border border-gray-100 dark:border-gray-800 p-4">
                        <item.icon size={17} className={`${item.color} mb-2`} />
                        <p className={`text-xl font-black ${item.color}`}>{item.value}</p>
                        <p className="text-xs text-gray-500 dark:text-gray-400 mt-0.5">{item.label}</p>
                      </div>
                    ))}
                  </div>
                ) : (
                  <div className="p-4 border-t border-gray-50 dark:border-gray-800 bg-slate-50/60 dark:bg-gray-950/20">
                    <div className="rounded-2xl border border-dashed border-gray-200 dark:border-gray-700 p-4 text-sm text-gray-500 dark:text-gray-400">
                      {/* Say whether facial signals were read; a failed read gets neither claim. */}
                      {!retrieved
                        ? "This week's signal data couldn't be loaded just now — the figures above are unaffected."
                        : <>
                            {faceIncluded(signals)
                              ? 'No weekly EEG or facial-recognition signal data yet.'
                              : 'No weekly EEG signal data yet, and facial signals were not read.'}
                            {' '}Open the full report after the student completes an AI session.
                          </>}
                    </div>
                  </div>
                )}

                <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between px-5 py-3 border-t border-gray-50 dark:border-gray-800 bg-emerald-50/50 dark:bg-emerald-900/10">
                  <div className="flex items-start gap-2 text-xs text-gray-500 dark:text-gray-400">
                    <ShieldCheck size={15} className="text-emerald-600 mt-0.5 shrink-0" />
                    <span>Signals are learning-state indicators only, not medical or diagnostic data.</span>
                  </div>
                  <Link to={`/parent/child/${child.user_id}`} state={{ name: child.name || null }} className="inline-flex items-center gap-1.5 text-xs font-bold text-emerald-700 dark:text-emerald-300 hover:underline">
                    <Sparkles size={14} /> View strategies
                  </Link>
                </div>

                {/* `null` is a failed read; an empty list is a child with no sessions yet. */}
                {child.sessions === null && (
                  <p className="px-5 py-3 border-t border-gray-50 dark:border-gray-800 text-xs text-gray-600 dark:text-gray-400">
                    Recent sessions couldn't be loaded just now.
                  </p>
                )}
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
                              <p className="text-xs text-gray-600 dark:text-gray-400">{new Date(s.started_at).toLocaleDateString()}</p>
                            </div>
                            <div className="text-right">
                              <p className={`text-sm font-black ${sAcc >= 70 ? 'text-green-500' : sAcc >= 40 ? 'text-amber-500' : 'text-rose-500'}`}>{sAcc}%</p>
                              <p className="text-xs text-gray-600 dark:text-gray-400">{s.questions_answered}q</p>
                            </div>
                          </div>
                        )
                      })}
                    </div>
                  </div>
                )}
              </m.div>
            )
          })}

          <Link to="/parent/link" className="flex items-center justify-center gap-2 p-4 bg-white dark:bg-gray-900 rounded-2xl border-2 border-dashed border-gray-200 dark:border-gray-700 text-gray-600 hover:border-emerald-400 hover:text-emerald-600 transition font-semibold text-sm dark:text-gray-400">
            <Users size={16} /> Link another child
          </Link>
        </div>
      )}
    </div>
  )
}
