import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { motion } from 'framer-motion'
import { Users, ArrowUpRight, TrendingUp, BookOpen, Flame, Brain, Zap, Eye, Activity, Sparkles, ShieldCheck } from 'lucide-react'
import { apiFetch } from '../../lib/api'
import { useAuth } from '../../context/AuthContext'
// pct, emotionOn are shared rather than redefined here: this page had verbatim
// copies of both, which is how it kept a fixed weakness in one after the other
// was patched. emotionOn -- whether a payload in hand was built with the
// emotion channel in it -- is aliased to faceIncluded, this page's existing
// name for the same check.
import { pct, valueOrReason, emotionOn as faceIncluded } from '../../components/signals/SignalPanel'

// Exactly the values the tiles below can render -- deliberately not every
// field the summary carries. engagement is absent because this page has no
// engagement tile (the full report does); counting it here would let a child
// whose only reading is engagement through to a row of four N/As, which is the
// "something is broken" display this check exists to avoid. Keep this list and
// the tiles below in step.
function hasSignalSummary(summary) {
  // `face_attention` was in this list while a Face Attention tile existed.
  // The tile is gone -- the column has no producer -- so counting it would let
  // a child whose only reading is attention through to a card with no facial
  // tile to show, which is the same failure the engagement note above
  // describes. Keep this list and the tiles below in step.
  return Boolean(summary && (summary.sessions > 0 || summary.focus != null || summary.stress != null))
}

// Whether the aggregate behind those values was actually read. The endpoint
// swallows a failed summary query so one broken read does not blank the
// dashboard, and answers 200 with defaults -- which every check above reads as
// a quiet week. Without this the page told a parent their child had recorded
// nothing whenever the RPC was broken. Absent on payloads predating the field,
// which came from a working read by definition.
function signalsRetrieved(summary) {
  return summary?.retrieved !== false
}

export default function ParentDashboard() {
  const { user } = useAuth()
  const [children, setChildren]   = useState([])
  const [loading, setLoading]     = useState(true)
  const [error, setError]         = useState(false)
  const name = user?.email?.split('@')[0] || 'there'

  useEffect(() => {
    let cancelled = false
    apiFetch('/api/parent/children')
      .then(c => { if (!cancelled) { setChildren(c || []); setError(false); setLoading(false) } })
      .catch(() => { if (!cancelled) { setError(true); setLoading(false) } })
    // Kept: an unmount mid-flight would otherwise set state on a gone
    // component. There is no longer a toggle to re-run this.
    return () => { cancelled = true }
  }, [])

  return (
    <div className="p-6 lg:p-8 pb-12 space-y-8">
      <motion.div initial={{ opacity: 0, y: -10 }} animate={{ opacity: 1, y: 0 }}>
        <h1 className="text-3xl font-black text-gray-900 dark:text-white">Hey, <span className="text-emerald-600">{name}</span> 👋</h1>
        <p className="text-gray-500 dark:text-gray-400 mt-1">Here's how your {children.length === 1 ? 'child is' : 'children are'} doing this week.</p>
      </motion.div>

      {/* A failed refresh with rows already on screen is a banner, not a
          takeover. This effect re-runs when the facial switch flips, so the
          whole-page error used to replace a screenful of academic stats and
          sessions -- none of which the switch has anything to do with -- over
          one failed request. The rows that are up were scrubbed of facial data
          when the switch moved, so what stays visible still honours it. */}
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
            const signals = child.signal_summary || {}
            const retrieved = signalsRetrieved(signals)
            // Only render the tiles from figures that were actually read. A
            // failed aggregate leaves them all at their defaults, which
            // hasSignalSummary reads as a quiet week.
            const showSignals = retrieved && hasSignalSummary(signals)
            const initial = (child.name || child.email || '?')[0].toUpperCase()
            return (
              <motion.div key={child.user_id}
                initial={{ opacity: 0, y: 16 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: i * 0.1 }}
                className="bg-white dark:bg-gray-900 rounded-2xl border border-gray-100 dark:border-gray-800 shadow-sm overflow-hidden">

                <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between p-5 border-b border-gray-50 dark:border-gray-800">
                  <div className="flex items-center gap-3">
                    <div className="w-12 h-12 bg-gradient-to-br from-emerald-400 to-teal-500 rounded-xl flex items-center justify-center text-white font-black text-lg shadow">
                      {initial}
                    </div>
                    <div>
                      <h3 className="font-black text-gray-900 dark:text-white">{child.name || 'Student'}</h3>
                      <p className="text-xs text-gray-400">{child.email || 'No email available'}</p>
                    </div>
                  </div>
                  <Link to={`/parent/child/${child.user_id}`}>
                    <motion.div whileHover={{ scale: 1.04 }} whileTap={{ scale: 0.97 }}
                      className="flex items-center justify-center gap-1.5 px-4 py-2 bg-emerald-50 dark:bg-emerald-900/30 text-emerald-700 dark:text-emerald-300 rounded-xl text-sm font-bold hover:bg-emerald-100 transition">
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
                      <p className={`text-2xl font-black ${s.color}`}>{s.value}</p>
                      <p className="text-xs text-gray-500 dark:text-gray-400 mt-0.5">{s.label}</p>
                    </div>
                  ))}
                </div>

                {showSignals ? (
                  <div className="grid grid-cols-2 lg:grid-cols-4 gap-3 p-4 border-t border-gray-50 dark:border-gray-800 bg-slate-50/60 dark:bg-gray-950/20">
                    {[
                      { icon: Brain,    label: 'Weekly Focus',   value: pct(signals.focus),          color: 'text-emerald-600' },
                      { icon: Zap,      label: 'Weekly Stress',  value: pct(signals.stress),         color: 'text-rose-600' },
                      // Never a raw "N/A" for a channel that was not recorded,
                      // and never a raw "N/A" for one that was recorded but
                      // produced nothing usable this week -- `valueOrReason`
                      // (SignalPanel.jsx) picks between withdrawn, unavailable,
                      // calibrating and no-sensor so this tile can't fall back
                      // to the generic string the rest of the reporting surfaces
                      // stopped showing.
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
                      {/* Naming facial recognition with the switch off would
                          report an absence that was never measured -- the same
                          distinction the weekly report's summary draws.
                          Saying nothing at all about it is not enough either:
                          hasSignalSummary reached "no data" without consulting
                          any facial reading, and the copy has to be clear that
                          is the scope of the claim rather than leaving a
                          parent to read it as covering everything.

                          A failed read is not an absence at all, so it gets
                          neither claim. Nothing was measured to report on, and
                          the invitation to open the full report would send a
                          parent to a page reading from the same aggregate. */}
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
                  <Link to={`/parent/child/${child.user_id}`} className="inline-flex items-center gap-1.5 text-xs font-bold text-emerald-700 dark:text-emerald-300 hover:underline">
                    <Sparkles size={14} /> View strategies
                  </Link>
                </div>

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
