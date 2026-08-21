import { useEffect, useRef, useState } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { supabase } from '../../lib/supabase'
import { Users, Search, ChevronDown, Flame, Brain, Smile, Target, TrendingUp, Zap, Heart, Activity } from 'lucide-react'
import HideSensorDataToggle from '../../components/common/HideSensorDataToggle'
import { readHideSensorData, writeHideSensorData } from '../../lib/viewPrefs'
import { apiFetch } from '../../lib/api'

// Matches the weekly report's window, so a teacher and a parent see the same week.
const SIGNAL_WINDOW_DAYS = 7
// Shown per-tile rather than as one heading, since Total Accuracy and Current
// Streak are lifetime figures (from user_stats), not part of this window.
const WINDOW_NOTE = `last ${SIGNAL_WINDOW_DAYS}d`
// A failed request must not read as "no data" -- every count defaults to 0 on failure.
const SIGNALS_UNAVAILABLE = 'signal data unavailable'
const eegSub = (n, failed) => {
  if (failed) return SIGNALS_UNAVAILABLE
  return n ? `${n} EEG readings · ${WINDOW_NOTE}` : `no EEG data · ${WINDOW_NOTE}`
}
const faceSub = (n, text, failed) => {
  if (failed) return SIGNALS_UNAVAILABLE
  return n ? `${text} · ${WINDOW_NOTE}` : `no face data · ${WINDOW_NOTE}`
}

// Signals are stored as 0..1 ratios, so this scales to a percent like the rest
// of the app (Live.jsx's Gauge, SignalPanel's pct). Returns null for
// missing values rather than 0 or NaN, which the tiles render as "—".
const asPct = (value) => {
  if (value === null || value === undefined) return null
  const n = Number(value)
  return Number.isFinite(n) ? `${Math.round(n * 100)}%` : null
}

// Reads student stats from user_stats, the signal-summary endpoint, and topic
// performance.
//
// user_stats and topic performance go through the browser client, so RLS
// applies. Signal averages instead go through /api/students/{id}/signal-summary
// -- a Postgres aggregate over the whole window rather than a capped direct
// read, since capping cognitive_signals/face_signals at 200 rows only covers
// the first few minutes at typical poll rates.
//
// The "Hide sensor data" switch on this page only affects what's displayed,
// not what's requested -- consent decides that server-side. See lib/viewPrefs.js.
async function getStudentStats(studentId)
{
   const [statsRes, summary, topicRes] = await Promise.all([
    // Goes through the endpoint, not a direct user_stats read: that table only
    // gains a row when a session closes, so a direct read would show "0
    // questions" for a student mid-session. The endpoint adds open-session counts.
    // Caught to a marked failure ({ retrieved: false }) rather than left to
    // reject the whole Promise.all, which would blank the signal tiles too.
    apiFetch(`/api/stats/student/${studentId}`)
      .catch(err => { console.error('Failed to load student stats:', err); return { retrieved: false } }),
    // Caught separately so a signal-summary outage doesn't cost the academic tiles.
    apiFetch(`/api/students/${studentId}/signal-summary?days=${SIGNAL_WINDOW_DAYS}`)
      .catch(err => { console.error('Failed to load signal summary:', err); return null }),
    supabase.from('user_math_performance')
      .select('topic_id, attempted_questions, correct_questions, math_topics(topic_name)')
      .eq('user_id', studentId)
  ])

  if (topicRes.error) console.error('Failed to load topic performance:', topicRes.error)

  const userStats = statsRes
  const signals = summary || {}
  // A failed read must not render as "0 questions, 0%" -- that would look measured.
  const statsRetrieved = userStats?.retrieved !== false

  const totalAccuracy = statsRetrieved && userStats && userStats.total_questions > 0
    ? Math.round((userStats.total_correct / userStats.total_questions) * 100)
    : null

  return {
    statsRetrieved,
    totalAccuracy,
    totalQuestions: statsRetrieved ? (userStats?.total_questions ?? 0) : null,
    currentStreak: statsRetrieved ? (userStats?.current_streak ?? 0) : null,
    bestStreak: statsRetrieved ? (userStats?.best_streak ?? 0) : null,
    focusScore: asPct(signals.focus),
    stressLevel: asPct(signals.stress),
    engagement: asPct(signals.engagement),
    dominantEmotion: signals.dominant_emotion ?? null,
    signalCount: signals.cognitive_samples ?? 0,
    faceSignalCount: signals.face_samples ?? 0,
    // bpm and ms are absolute units, not ratios, so they skip asPct.
    heartRate: typeof signals.heart_rate_bpm === 'number' ? Math.round(signals.heart_rate_bpm) : null,
    rmssd: typeof signals.rmssd_ms === 'number' ? Math.round(signals.rmssd_ms) : null,
    heartSamples: signals.heart_samples ?? 0,
    // Server-decided from consent: "sensor off" is different from "nothing recorded".
    heartIncluded: signals.heart_included === true,
    // True if the request itself failed (summary === null) or the endpoint's
    // aggregate query failed (retrieved === false) -- either way, the zero
    // counts above don't mean the student recorded nothing.
    signalsFailed: summary === null || signals.retrieved === false,
    // Server-decided from consent, like heartIncluded: "camera off" vs "nothing recorded this week".
    faceIncluded: signals.emotion_included !== undefined
      ? signals.emotion_included !== false
      : signals.face_included !== false,
    topics: (topicRes.data || []).map(row => {
      const attempted = row.attempted_questions || 0
      const correct = row.correct_questions || 0
      return {
        topicId: row.topic_id,
        topicName: row.math_topics?.topic_name || 'unknown',
        attempted,
        correct,
        // null, not 0: an untouched topic isn't the same as one scored zero.
        accuracy: attempted ? Math.round((correct / attempted) * 100) : null,
      }
    })
  }
}

export default function Students() {
  const [students, setStudents] = useState([])
  const [loading, setLoading]   = useState(true)
  const [search, setSearch]     = useState('')
  const [expandedId, setExpandedId] = useState(null)
  const [statsCache, setStatsCache] = useState({})
  const [statsLoading, setStatsLoading] = useState({})
  // Same stored preference as the student progress report, so the switch is consistent across pages.
  const [hideSensors, setHideSensors] = useState(readHideSensorData)
  // Per-student request id: a stale in-flight fetch could otherwise overwrite a newer result.
  const statsRequestIds = useRef({})

  useEffect(() => {
    // pull users who registered with the 'student' role
    let cancelled = false;

    async function loadStudents()
    {
      // find what teacher is logged in
      
      const {data: { user }, error: userError } = await supabase.auth.getUser()
      if (userError || !user )
      {
        if(!cancelled) setLoading(false)
        return 
      }
      // pull students enrolled in any class taught by teacher.
      const {data, error} = await supabase
      .from('class_memberships')
      .select('student_id, profiles!inner(*), classes!inner(teacher_id)')
      .eq('classes.teacher_id', user.id)

      if (error) console.error('Failed to load students:', error)

      if(cancelled)
        return

      if (error) 
      {
        console.error('Failed to load students:', error )
        setLoading(false)
        return
      }
    // Get rid of duplicate students
    const seen = new Map()
    for( const row of data || [])
    {
      if(row.profiles && !seen.has(row.student_id))
        seen.set(row.student_id, row.profiles)
    }

    setStudents(Array.from(seen.values()))
    setLoading(false)
  }

  loadStudents()
  return () => { cancelled = true}
    // supabase
    //   .from('profiles')
    //   .select('*')
    //   .eq('role', 'student')
    //   .then(({ data, error }) => {
    //     if (!error) setStudents(data || [])
    //     setLoading(false)
    //   })
    //   .catch(() => setLoading(false))
  }, [])

  const filtered = students.filter(s =>
    (s.email || s.username || s.id || '').toLowerCase().includes(search.toLowerCase())
  )

  async function toggleExpand(studentId){
    if (expandedId === studentId) {
      setExpandedId(null)
      return
    }
    setExpandedId(studentId)
    // A cached row whose academic read failed isn't treated as loaded, so
    // collapsing and re-expanding retries it instead of getting stuck.
    const cached = statsCache[studentId]
    if ((cached && cached.statsRetrieved !== false) || statsLoading[studentId]) return
    await refreshStats(studentId)
  }

  async function refreshStats(studentId) {
    const requestId = (statsRequestIds.current[studentId] || 0) + 1
    statsRequestIds.current[studentId] = requestId
    setStatsLoading(prev => ({ ...prev, [studentId]: true }))
    let stats
    try {
      stats = await getStudentStats(studentId)
    } catch (err) {
      // Must clear the loading flag even on failure, or the row's spinner never goes away.
      console.error('Failed to load student stats:', err)
      if (requestId === statsRequestIds.current[studentId]) {
        setStatsLoading(prev => ({ ...prev, [studentId]: false }))
      }
      return
    }
    // Discard a superseded result -- an older in-flight request landing late must not overwrite newer data.
    if (requestId !== statsRequestIds.current[studentId]) return
    setStatsLoading(prev => ({ ...prev, [studentId]: false }))
    setStatsCache(prev => ({ ...prev, [studentId]: stats }))
  }

  function handleHideSensorsChange(next) {
    setHideSensors(next)
    writeHideSensorData(next)
    // No cache drop or re-fetch needed: this only changes what's displayed, not what was requested.
  }


  return (
    <div className="p-6 lg:p-8 pb-12">
      <motion.div initial={{ opacity: 0, y: -10 }} animate={{ opacity: 1, y: 0 }} className="mb-6">
        <h1 className="text-3xl font-black text-gray-900 dark:text-white flex items-center gap-3">
          <Users className="text-violet-600" size={28} /> Students
        </h1>
        <p className="text-gray-500 dark:text-gray-400 mt-1">Students enrolled in your classes.</p>
      </motion.div>

      <div className="relative mb-6 max-w-sm">
        <Search size={16} className="absolute left-3.5 top-1/2 -translate-y-1/2 text-gray-400" />
        <input value={search} onChange={e => setSearch(e.target.value)}
          className="w-full pl-10 pr-4 py-2.5 bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-700 rounded-xl text-sm dark:text-white outline-none focus:ring-2 focus:ring-violet-500 transition"
          placeholder="Search students..." />
      </div>

      <div className="mb-6">
        <HideSensorDataToggle hidden={hideSensors} onChange={handleHideSensorsChange} />
      </div>

      {loading ? (
        <div className="space-y-3">{[1,2,3,4,5].map(i => <div key={i} className="h-16 bg-white dark:bg-gray-900 rounded-2xl border border-gray-100 dark:border-gray-800 animate-pulse" />)}</div>
      ) : filtered.length === 0 ? (
        <div className="text-center py-16">
          <div className="text-6xl mb-4">🎓</div>
          <h3 className="text-xl font-black text-gray-900 dark:text-white mb-2">
            {students.length === 0 ? 'No students yet' : 'No results'}
          </h3>
          <p className="text-gray-500 dark:text-gray-400 text-sm">
            {students.length === 0
              ? 'Students appear here once they join one of your classes with its join code.'
              : 'Try a different search term.'}
          </p>
        </div>
      ) : (
        <div className="bg-white dark:bg-gray-900 rounded-2xl border border-gray-100 dark:border-gray-800 shadow-sm overflow-hidden">
          <div className="grid grid-cols-4 px-5 py-3 border-b border-gray-50 dark:border-gray-800">
            <span className="text-xs font-bold uppercase tracking-widest text-gray-400 col-span-2">Student</span>
            <span className="text-xs font-bold uppercase tracking-widest text-gray-400">Joined</span>
            <span className="text-xs font-bold uppercase tracking-widest text-gray-400 text-right">Role</span>
          </div>
          {filtered.map((s, i) => {
            const initial = (s.email || s.username || s.id || '?')[0].toUpperCase()
            const name    = s.username || s.email?.split('@')[0] || s.id?.slice(0, 8)
            const joined  = s.created_at ? new Date(s.created_at).toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' }) : '—'
            const isOpen = expandedId === s.id
            const isLoadingStats = !!statsLoading[s.id]
            const stats = statsCache[s.id]
            return (
              <div key={s.id} className="border-b border-gray-50 dark:border-gray-800 last:border-0">
                <motion.button
                  type="button"
                  onClick={() => toggleExpand(s.id)}
                  initial={{ opacity: 0, x: -8 }} animate={{ opacity: 1, x: 0 }} transition={{ delay: i * 0.03 }}
                  whileHover={{ x: 3 }}
                  className="w-full grid grid-cols-4 items-center px-5 py-4 hover:bg-slate-50 dark:hover:bg-gray-800 transition-colors text-left"
                >
                  <div className="flex items-center gap-3 col-span-2">
                    <div className="w-9 h-9 bg-gradient-to-br from-violet-400 to-purple-500 rounded-full flex items-center justify-center text-white text-xs font-black flex-shrink-0">
                      {initial}
                    </div>
                    <div>
                      <p className="text-sm font-bold text-gray-900 dark:text-white">{name}</p>
                      {s.email && <p className="text-xs text-gray-400">{s.email}</p>}
                    </div>
                  </div>
                  <p className="text-sm text-gray-500 dark:text-gray-400">{joined}</p>
                  <div className="flex justify-end items-center gap-3">
                    <span className="text-xs font-bold px-2.5 py-1 bg-indigo-100 dark:bg-indigo-900/40 text-indigo-700 dark:text-indigo-300 rounded-full">Student</span>
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
                            {[1,2,3,4].map(k => <div key={k} className="h-20 bg-white dark:bg-gray-900 rounded-xl border border-gray-100 dark:border-gray-800 animate-pulse" />)}
                          </div>
                        ) : !stats ? (
                          <p className="text-sm text-gray-400">Couldn't load stats for this student.</p>
                        ) : (
                          <>
                            <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mb-2">
                              <MiniStat
                                icon={<TrendingUp size={16} />}
                                label="Total Accuracy"
                                value={stats.totalAccuracy !== null ? `${stats.totalAccuracy}%` : '—'}
                                sub={stats.statsRetrieved
                                  ? `${stats.totalQuestions} questions`
                                  : "couldn't be loaded"}
                                color="indigo"
                              />
                              {!hideSensors && (
                                <>
                                  <MiniStat
                                    icon={<Flame size={16} />}
                                    label="Stress Level"
                                    value={stats.stressLevel ?? '—'}
                                    sub={eegSub(stats.signalCount, stats.signalsFailed)}
                                    color="rose"
                                  />
                                  <MiniStat
                                    icon={<Target size={16} />}
                                    label="Focus Score"
                                    value={stats.focusScore ?? '—'}
                                    sub={eegSub(stats.signalCount, stats.signalsFailed)}
                                    color="emerald"
                                  />
                                </>
                              )}
                              <MiniStat
                                icon={<Zap size={16} />}
                                label="Current Streak"
                                value={stats.statsRetrieved ? stats.currentStreak : '—'}
                                sub={stats.statsRetrieved
                                  ? `best: ${stats.bestStreak}`
                                  : "couldn't be loaded"}
                                color="amber"
                              />
                            </div>

                            {/* Gated by "Hide sensor data" -- a display preference, doesn't change what was fetched. */}
                            {!hideSensors && (
                            <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mt-3">
                              <MiniStat
                                icon={<Brain size={16} />}
                                label="Engagement"
                                value={stats.engagement ?? '—'}
                                sub={eegSub(stats.signalCount, stats.signalsFailed)}
                                color="indigo"
                              />
                              {/* "Off" means the viewer turned off facial reporting; different from no reading. */}
                              <MiniStat
                                icon={<Smile size={16} />}
                                label="Dominant Emotion"
                                value={stats.faceIncluded ? (stats.dominantEmotion ?? '—') : 'Off'}
                                sub={stats.faceIncluded
                                  ? faceSub(stats.faceSignalCount, 'most frequent', stats.signalsFailed)
                                  : 'reporting off'}
                                color="violet"
                              />
                            </div>
                            )}

                            {/* Heart rate/HRV in absolute units, own row. "Off" distinguishes a disabled sensor from one that recorded nothing. */}
                            {!hideSensors && (
                            <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mt-3">
                              <MiniStat
                                icon={<Heart size={16} />}
                                label="Avg Heart Rate"
                                value={stats.heartIncluded ? (stats.heartRate !== null ? `${stats.heartRate} bpm` : '—') : 'Off'}
                                sub={stats.heartIncluded
                                  ? `${stats.heartSamples} readings`
                                  : 'not recorded'}
                                color="rose"
                              />
                              <MiniStat
                                icon={<Activity size={16} />}
                                label="Avg HRV"
                                value={stats.heartIncluded ? (stats.rmssd !== null ? `${stats.rmssd} ms` : '—') : 'Off'}
                                sub={stats.heartIncluded
                                  ? 'RMSSD, when measurable'
                                  : 'not recorded'}
                                color="amber"
                              />
                            </div>
                            )}

                            {stats.topics.length > 0 && (
                              <div className="mt-4 bg-white dark:bg-gray-900 rounded-xl border border-gray-100 dark:border-gray-800 p-4">
                                <p className="text-xs font-bold uppercase tracking-widest text-gray-400 mb-3">Per-topic accuracy</p>
                                <div className="grid sm:grid-cols-2 gap-3">
                                  {stats.topics.map(t => (
                                    <div key={t.topicId ?? t.topicName}>
                                      <div className="flex items-center justify-between mb-1">
                                        <span className="text-xs font-semibold text-gray-600 dark:text-gray-400 capitalize">
                                          {t.topicName.replaceAll('_', ' ')}
                                        </span>
                                        <span className="text-xs font-black text-gray-900 dark:text-white">
                                          {t.accuracy === null ? 'not attempted' : `${t.accuracy}%`}
                                        </span>
                                      </div>
                                      <div className="h-2 rounded-full bg-gray-100 dark:bg-gray-800 overflow-hidden">
                                        <div className="h-full rounded-full bg-violet-500" style={{ width: `${t.accuracy ?? 0}%` }} />
                                      </div>
                                      <p className="text-[10px] text-gray-400 mt-0.5">{t.correct}/{t.attempted} correct</p>
                                    </div>
                                  ))}
                                </div>
                              </div>
                            )}

                            {/* Both branches only assert "no activity" once the relevant data was actually read
                                -- not when the switch skipped facial data or the summary request failed. */}
                            {stats.signalsFailed ? (
                              <p className="text-xs text-gray-400 mt-3">
                                Signal data couldn&apos;t be loaded — the figures above cover questions only.
                              </p>
                            ) : stats.totalQuestions === 0 && stats.signalCount === 0 &&
                                (!stats.faceIncluded || stats.faceSignalCount === 0) ? (
                              <p className="text-xs text-gray-400 mt-3">
                                {stats.faceIncluded
                                  ? "This student hasn't completed any sessions yet."
                                  : 'No question or EEG activity yet — facial signals were not read.'}
                              </p>
                            ) : null}
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

function MiniStat({ icon, label, value, sub, color }) {
  const colorMap = {
    indigo: 'bg-indigo-100 dark:bg-indigo-900/40 text-indigo-700 dark:text-indigo-300',
    rose:   'bg-rose-100 dark:bg-rose-900/40 text-rose-700 dark:text-rose-300',
    emerald:'bg-emerald-100 dark:bg-emerald-900/40 text-emerald-700 dark:text-emerald-300',
    amber:  'bg-amber-100 dark:bg-amber-900/40 text-amber-700 dark:text-amber-300',
    sky:    'bg-sky-100 dark:bg-sky-900/40 text-sky-700 dark:text-sky-300',
    violet: 'bg-violet-100 dark:bg-violet-900/40 text-violet-700 dark:text-violet-300',
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