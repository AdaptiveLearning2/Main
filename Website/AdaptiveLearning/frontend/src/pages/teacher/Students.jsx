import { useEffect, useRef, useState } from 'react'
import { m, AnimatePresence } from 'framer-motion'
import { Users, Search, ChevronDown, Flame, Smile, Target, TrendingUp, Zap, Heart, Activity } from 'lucide-react'
import HideSensorDataToggle from '../../components/common/HideSensorDataToggle'
import LoadError from '../../components/ui/LoadError'
import { readHideSensorData, writeHideSensorData } from '../../lib/viewPrefs'
import { apiFetch } from '../../lib/api'
import { offLabel } from '../../lib/signalFormat'
import { stagger } from '../../lib/stagger'

// Matches the weekly report's window.
const SIGNAL_WINDOW_DAYS = 7
// Per tile, since accuracy and streak are lifetime figures, not this window.
const WINDOW_NOTE = `last ${SIGNAL_WINDOW_DAYS}d`
const SIGNALS_UNAVAILABLE = 'signal data unavailable'
const CONSENT_UNREAD = "consent couldn't be read"
const NO_NAME = 'No name set'
const eegSub = (n, failed) => {
  if (failed) return SIGNALS_UNAVAILABLE
  return n ? `${n} EEG readings · ${WINDOW_NOTE}` : `no EEG data · ${WINDOW_NOTE}`
}
const faceSub = (n, text, failed) => {
  if (failed) return SIGNALS_UNAVAILABLE
  return n ? `${text} · ${WINDOW_NOTE}` : `no face data · ${WINDOW_NOTE}`
}

// 0..1 ratio to a percent; null (rendered "—") for a missing value, never 0 or NaN.
const asPct = (value) => {
  if (value === null || value === undefined) return null
  const n = Number(value)
  return Number.isFinite(n) ? `${Math.round(n * 100)}%` : null
}

const isTextOrNull = (v) => v === null || typeof v === 'string'

// Any other shape is a failed read, never a roster: an id to key on, a name and an email (null when unset).
function rosterRows(body) {
  const ok = Array.isArray(body) && body.every(r =>
    typeof r?.user_id === 'string' && r.user_id !== '' && isTextOrNull(r.name) && isTextOrNull(r.email))
  if (!ok) {
    // The backend answered, so LoadError's 503 sentence ("just now"), not "make sure the backend is running".
    throw Object.assign(new Error('/api/teacher/students answered with an unexpected shape'), { status: 503 })
  }
  return body
}

// Stats, the signal summary (a server-side aggregate, not a capped read) and
// topic performance. "Hide sensor data" affects display only, not the request.
async function getStudentStats(studentId)
{
   const [statsRes, summary, topicRows] = await Promise.all([
    // The endpoint adds open-session counts that user_stats lacks mid-session.
    // Caught per read so one failure can't blank the other tiles.
    apiFetch(`/api/stats/student/${studentId}`)
      .catch(err => { console.error('Failed to load student stats:', err); return { retrieved: false } }),
    apiFetch(`/api/students/${studentId}/signal-summary?days=${SIGNAL_WINDOW_DAYS}`)
      .catch(err => { console.error('Failed to load signal summary:', err); return null }),
    // No topics panel on a failure, which claims nothing either way.
    apiFetch(`/api/performance/student/${studentId}`)
      .catch(err => { console.error('Failed to load topic performance:', err); return [] }),
  ])

  const userStats = statsRes
  const signals = summary || {}
  // A failed read must not render as "0 questions, 0%".
  const statsRetrieved = userStats?.retrieved !== false

  const totalAccuracy = statsRetrieved && userStats && userStats.total_questions > 0
    ? Math.round((userStats.total_correct / userStats.total_questions) * 100)
    : null
  const signalsFailed = summary === null || signals.retrieved === false
  // A failed read hides a figure, never a revocation the consent read did return.
  const failedWhileOn = (on) => signalsFailed && !(signals.consent_retrieved === true && !on)

  return {
    statsRetrieved,
    totalAccuracy,
    totalQuestions: statsRetrieved ? (userStats?.total_questions ?? 0) : null,
    currentStreak: statsRetrieved ? (userStats?.current_streak ?? 0) : null,
    bestStreak: statsRetrieved ? (userStats?.best_streak ?? 0) : null,
    focusScore: asPct(signals.focus),
    stressLevel: asPct(signals.stress),
    dominantEmotion: signals.dominant_emotion ?? null,
    signalCount: signals.cognitive_samples ?? 0,
    faceSignalCount: signals.face_samples ?? 0,
    // bpm and ms are absolute units, so no asPct.
    heartRate: typeof signals.heart_rate_bpm === 'number' ? Math.round(signals.heart_rate_bpm) : null,
    rmssd: typeof signals.rmssd_ms === 'number' ? Math.round(signals.rmssd_ms) : null,
    heartSamples: signals.heart_samples ?? 0,
    // Server-decided from consent: "sensor off" is not "nothing recorded".
    heartIncluded: signals.heart_included === true,
    // Request failed or the aggregate failed; the zero counts above mean nothing then.
    signalsFailed,
    eegFailed: failedWhileOn(signals.eeg_enabled !== false),
    faceFailed: failedWhileOn(signals.emotion_included !== false && signals.face_included !== false),
    heartFailed: failedWhileOn(signals.heart_included === true),
    // Server-decided from consent, like heartIncluded.
    faceIncluded: signals.emotion_included !== undefined
      ? signals.emotion_included !== false
      : signals.face_included !== false,
    // False when the consent read failed: then no tile may say a sensor is off.
    consentRetrieved: signals.consent_retrieved !== false,
    // What each tile says with no figure, as every signal tile does (`offLabel`).
    eegOff: offLabel({ on: signals.eeg_enabled !== false, revokedAt: signals.eeg_revoked_at,
                       consentRetrieved: signals.consent_retrieved,
                       samples: signals.cognitive_samples }),
    faceOff: offLabel({ on: signals.emotion_included !== false && signals.face_included !== false,
                        revokedAt: signals.emotion_revoked_at,
                        consentRetrieved: signals.consent_retrieved, samples: signals.face_samples }),
    heartOff: offLabel({ on: signals.heart_included === true, revokedAt: signals.heart_revoked_at,
                         consentRetrieved: signals.consent_retrieved, samples: signals.heart_samples }),
    topics: (Array.isArray(topicRows) ? topicRows : []).map(row => {
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
  // A failed roster read is not an empty class: "No students yet" would tell the teacher nobody joined.
  const [rosterError, setRosterError] = useState(null)
  const [rosterAttempt, setRosterAttempt] = useState(0)
  const [search, setSearch]     = useState('')
  const [expandedId, setExpandedId] = useState(null)
  const [statsCache, setStatsCache] = useState({})
  const [statsLoading, setStatsLoading] = useState({})
  const [hideSensors, setHideSensors] = useState(readHideSensorData)
  // Per-student request id, so a stale fetch can't overwrite a newer result.
  const statsRequestIds = useRef({})

  useEffect(() => {
    let cancelled = false;

    // Every student in any class this teacher teaches, once: `{user_id, name, email, joined_at}`.
    async function loadStudents() {
      let rows
      try {
        rows = rosterRows(await apiFetch('/api/teacher/students'))
      } catch (e) {
        console.error('Failed to load students:', e)
        if (!cancelled) { setRosterError(e); setLoading(false) }
        return
      }
      if (cancelled) return
      setStudents(rows)
      setRosterError(null)
      setLoading(false)
    }

  loadStudents()
  return () => { cancelled = true}
  }, [rosterAttempt])

  const retryRoster = () => { setRosterError(null); setLoading(true); setRosterAttempt(n => n + 1) }

  // Search name and email both.
  const filtered = students.filter(s =>
    `${s.name || ''} ${s.email || ''} ${s.user_id || ''}`
      .toLowerCase().includes(search.toLowerCase())
  )

  async function toggleExpand(studentId){
    if (expandedId === studentId) {
      setExpandedId(null)
      return
    }
    setExpandedId(studentId)
    // A cached failed read isn't loaded, so re-expanding retries it.
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
      console.error('Failed to load student stats:', err)
      if (requestId === statsRequestIds.current[studentId]) {
        setStatsLoading(prev => ({ ...prev, [studentId]: false }))
      }
      return
    }
    if (requestId !== statsRequestIds.current[studentId]) return
    setStatsLoading(prev => ({ ...prev, [studentId]: false }))
    setStatsCache(prev => ({ ...prev, [studentId]: stats }))
  }

  function handleHideSensorsChange(next) {
    setHideSensors(next)
    writeHideSensorData(next)
  }


  return (
    <div className="p-6 lg:p-8 pb-12">
      <m.div initial={{ opacity: 0, y: -10 }} animate={{ opacity: 1, y: 0 }} className="mb-6">
        <h1 className="text-3xl font-black text-gray-900 dark:text-white flex items-center gap-3">
          <Users className="text-violet-600" size={28} /> Students
        </h1>
        <p className="text-gray-500 dark:text-gray-400 mt-1">Students enrolled in your classes.</p>
      </m.div>

      <div className="relative mb-6 max-w-sm">
        <Search size={16} className="absolute left-3.5 top-1/2 -translate-y-1/2 text-gray-600 dark:text-gray-400" />
        <input value={search} onChange={e => setSearch(e.target.value)}
          className="w-full pl-10 pr-4 py-2.5 bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-700 rounded-xl text-sm dark:text-white outline-none focus:ring-2 focus:ring-violet-500 transition"
          placeholder="Search students..." />
      </div>

      <div className="mb-6">
        <HideSensorDataToggle hidden={hideSensors} onChange={handleHideSensorsChange} />
      </div>

      {loading ? (
        <div className="space-y-3">{[1,2,3,4,5].map(i => <div key={i} className="h-16 bg-white dark:bg-gray-900 rounded-2xl border border-gray-100 dark:border-gray-800 animate-pulse" />)}</div>
      ) : rosterError ? (
        <LoadError what="your students" error={rosterError} onRetry={retryRoster} />
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
            <span className="text-xs font-bold uppercase tracking-widest text-gray-600 col-span-2 dark:text-gray-400">Student</span>
            <span className="text-xs font-bold uppercase tracking-widest text-gray-600 dark:text-gray-400">Joined</span>
            <span className="text-xs font-bold uppercase tracking-widest text-gray-600 text-right dark:text-gray-400">Role</span>
          </div>
          {filtered.map((s, i) => {
            // Never a made-up name: without one the email stands in, else the row says none is set.
            const label   = s.name || s.email || NO_NAME
            const sub     = s.name ? s.email : (s.email ? NO_NAME : null)
            // From the label being shown, so the letter and the label agree.
            const initial = s.name || s.email ? label[0].toUpperCase() : '?'
            // The earliest join across this teacher's classes, not the account's creation.
            const joined  = s.joined_at ? new Date(s.joined_at).toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' }) : '—'
            const isOpen = expandedId === s.user_id
            const isLoadingStats = !!statsLoading[s.user_id]
            const stats = statsCache[s.user_id]
            return (
              <div key={s.user_id} className="border-b border-gray-50 dark:border-gray-800 last:border-0">
                <m.button
                  type="button"
                  onClick={() => toggleExpand(s.user_id)}
                  initial={{ opacity: 0, x: -8 }} animate={{ opacity: 1, x: 0 }} transition={{ delay: stagger(i, 0.03) }}
                  whileHover={{ x: 3 }}
                  className="w-full grid grid-cols-4 items-center px-5 py-4 hover:bg-slate-50 dark:hover:bg-gray-800 transition-colors text-left"
                >
                  <div className="flex items-center gap-3 col-span-2">
                    <div className="w-9 h-9 bg-gradient-to-br from-violet-400 to-purple-500 rounded-full flex items-center justify-center text-white text-xs font-black flex-shrink-0">
                      {initial}
                    </div>
                    <div>
                      <p className="text-sm font-bold text-gray-900 dark:text-white">{label}</p>
                      {sub && <p className="text-xs text-gray-600 dark:text-gray-400">{sub}</p>}
                    </div>
                  </div>
                  <p className="text-sm text-gray-500 dark:text-gray-400">{joined}</p>
                  <div className="flex justify-end items-center gap-3">
                    <span className="text-xs font-bold px-2.5 py-1 bg-indigo-100 dark:bg-indigo-900/40 text-indigo-700 dark:text-indigo-300 rounded-full">Student</span>
                    <m.span animate={{ rotate: isOpen ? 180 : 0 }} transition={{ duration: 0.2 }}>
                      <ChevronDown size={16} className="text-gray-600 dark:text-gray-400" />
                    </m.span>
                  </div>
                </m.button>

                                <AnimatePresence initial={false}>
                  {isOpen && (
                    <m.div
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
                          <p className="text-sm text-gray-600 dark:text-gray-400">Couldn't load stats for this student.</p>
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
                                    value={stats.eegFailed ? '—' : (stats.stressLevel ?? stats.eegOff)}
                                    sub={eegSub(stats.signalCount, stats.eegFailed)}
                                    color="rose"
                                  />
                                  <MiniStat
                                    icon={<Target size={16} />}
                                    label="Focus Score"
                                    value={stats.eegFailed ? '—' : (stats.focusScore ?? stats.eegOff)}
                                    sub={eegSub(stats.signalCount, stats.eegFailed)}
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

                            {!hideSensors && (
                            <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mt-3">
                              {/* No Engagement tile: it is the focus index under another name. */}
                              {/* "Off" only for a channel consent says is off, never for a failed read. */}
                              <MiniStat
                                icon={<Smile size={16} />}
                                label="Dominant Emotion"
                                value={stats.faceFailed ? '—'
                                  : stats.faceIncluded ? (stats.dominantEmotion ?? stats.faceOff) : stats.faceOff}
                                sub={stats.faceFailed ? SIGNALS_UNAVAILABLE
                                  : stats.faceIncluded ? faceSub(stats.faceSignalCount, 'most frequent', false)
                                  : stats.consentRetrieved ? 'reporting off' : CONSENT_UNREAD}
                                color="violet"
                              />
                            </div>
                            )}

                            {/* "Off" is a disabled sensor, not one that recorded nothing. */}
                            {!hideSensors && (
                            <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mt-3">
                              <MiniStat
                                icon={<Heart size={16} />}
                                label="Avg Heart Rate"
                                value={stats.heartFailed ? '—' : stats.heartIncluded && stats.heartRate !== null
                                  ? `${stats.heartRate} bpm` : stats.heartOff}
                                sub={stats.heartFailed ? SIGNALS_UNAVAILABLE
                                  : stats.heartIncluded ? `${stats.heartSamples} readings`
                                  : stats.consentRetrieved ? 'not recorded' : CONSENT_UNREAD}
                                color="rose"
                              />
                              <MiniStat
                                icon={<Activity size={16} />}
                                label="Avg HRV"
                                value={stats.heartFailed ? '—' : stats.heartIncluded && stats.rmssd !== null
                                  ? `${stats.rmssd} ms` : stats.heartOff}
                                sub={stats.heartFailed ? SIGNALS_UNAVAILABLE
                                  : stats.heartIncluded ? 'RMSSD, when measurable'
                                  : stats.consentRetrieved ? 'not recorded' : CONSENT_UNREAD}
                                color="amber"
                              />
                            </div>
                            )}

                            {stats.topics.length > 0 && (
                              <div className="mt-4 bg-white dark:bg-gray-900 rounded-xl border border-gray-100 dark:border-gray-800 p-4">
                                <p className="text-xs font-bold uppercase tracking-widest text-gray-600 mb-3 dark:text-gray-400">Per-topic accuracy</p>
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
                                      <p className="text-[10px] text-gray-600 mt-0.5 dark:text-gray-400">{t.correct}/{t.attempted} correct</p>
                                    </div>
                                  ))}
                                </div>
                              </div>
                            )}

                            {/* Assert "no activity" only once the relevant data was actually read. */}
                            {stats.signalsFailed ? (
                              <p className="text-xs text-gray-600 mt-3 dark:text-gray-400">
                                Signal data couldn&apos;t be loaded — the figures above cover questions only.
                              </p>
                            ) : stats.totalQuestions === 0 && stats.signalCount === 0 &&
                                (!stats.faceIncluded || stats.faceSignalCount === 0) ? (
                              <p className="text-xs text-gray-600 mt-3 dark:text-gray-400">
                                {stats.faceIncluded
                                  ? "This student hasn't completed any sessions yet."
                                  : 'No question or EEG activity yet — facial signals were not read.'}
                              </p>
                            ) : null}
                          </>
                        )}
                      </div>
                    </m.div>
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
      <p className="text-[11px] text-gray-600 mt-1 dark:text-gray-400">{label}</p>
      {sub && <p className="text-[10px] text-gray-600 mt-0.5 dark:text-gray-400">{sub}</p>}
    </div>
  )
}