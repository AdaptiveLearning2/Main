import { useEffect, useRef, useState } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { supabase } from '../../lib/supabase'
import { Users, Search, ChevronDown, Flame, Brain, Smile, Target, TrendingUp, Zap, Heart, Activity } from 'lucide-react'
import HideSensorDataToggle from '../../components/common/HideSensorDataToggle'
import { readHideSensorData, writeHideSensorData } from '../../lib/viewPrefs'
import { apiFetch } from '../../lib/api'

// How far back the expanded row's signal averages look. Matches the weekly
// report and the summary RPCs' p_days default, so a teacher and a parent
// looking at the same student are describing the same week. Named rather than
// inlined because the copy below quotes it.
const SIGNAL_WINDOW_DAYS = 7
// Stated on the signal tiles themselves rather than as a heading over the grid:
// Total Accuracy and Current Streak sit in the same rows and come from
// user_stats, which is lifetime. A blanket "last 7 days" above all of them would
// be wrong about half its contents.
const WINDOW_NOTE = `last ${SIGNAL_WINDOW_DAYS}d`
// A failed summary request is not a quiet week, and the tiles must not read as
// one. Every count these subtitles quote comes back 0 when the request failed,
// so "no EEG data" would be asserting an absence in data that never loaded --
// the same distinction the report draws with signalError, and the same one the
// facial clause on the note below was narrowed to make.
const SIGNALS_UNAVAILABLE = 'signal data unavailable'
const eegSub = (n, failed) => {
  if (failed) return SIGNALS_UNAVAILABLE
  return n ? `${n} EEG readings · ${WINDOW_NOTE}` : `no EEG data · ${WINDOW_NOTE}`
}
const faceSub = (n, text, failed) => {
  if (failed) return SIGNALS_UNAVAILABLE
  return n ? `${text} · ${WINDOW_NOTE}` : `no face data · ${WINDOW_NOTE}`
}

// Hoisted out of the component: none of this reads component state, and the
// honest place for a data layer is not the render body.
//
// Signals are stored as 0..1 ratios in cognitive_signals and face_signals,
// which is the interpretation the original placeholders were waiting on. The
// rest of the app scales them by 100 at render (Live.jsx's Gauge,
// SignalPanel's pct), so this does too.
//
// null for anything that is not a measurement, which the tiles below render as
// "—". Guarding on the converted value rather than the raw one, because
// Number(null) is 0 and Number(undefined) is NaN: a field the summary did not
// carry would otherwise reach a teacher as a confident "0%" or as "NaN%". Same
// reasoning as SignalPanel's pct.
const asPct = (value) => {
  if (value === null || value === undefined) return null
  const n = Number(value)
  return Number.isFinite(n) ? `${Math.round(n * 100)}%` : null
}

// Function to retrieve student data from user_stats, the signal-summary
// endpoint and topic performance.
//
// user_stats and topic performance are read with the browser client, so RLS
// applies to them.
//
// The signal averages are not, and deliberately. They used to be a direct read
// of cognitive_signals and face_signals capped at 200 rows -- and at the
// poller's default 1 Hz that cap binds after roughly three minutes, so tiles
// labelled "last 7d" were averaging the newest three minutes of one sitting,
// with a reading count pinned at exactly 200 and presented as a count of the
// week. The parent dashboard, aggregating the same week in Postgres, showed
// different numbers for the same student. Raising the cap is not available:
// seven days at 1 Hz is upwards of half a million rows.
//
// So the four averages come from /api/students/{id}/signal-summary, which is
// the same student_signal_summary aggregate the parent dashboard reads --
// exact over the whole window, no rows transferred, and no cap to bind. The
// scope is unchanged: that endpoint's _can_view_student teacher branch encodes
// the same relationship as the "cog: teacher read" and "face: teacher read"
// policies this query used to lean on.
//
// No viewer-side flag is threaded in any more. What may be read is decided by
// stored consent, server-side; the "Hide sensor data" switch on this page is a
// display preference and does not change the request. See lib/viewPrefs.js.
async function getStudentStats(studentId)
{
   const [statsRes, summary, topicRes] = await Promise.all([
    // The endpoint, not `user_stats` directly. That table only gains a row when
    // a session *closes*, so reading it here showed "0 questions" for a student
    // who was answering right now -- while the parent's report, which goes
    // through this endpoint, showed the real figure for the same child at the
    // same moment. `/api/stats/student` adds any open session's counts and
    // applies `_verify_can_view_student`, which the direct read leaned on RLS
    // for.
    // Caught, but *not* to null. An uncaught rejection here took the whole
    // Promise.all down and blanked the four signal tiles beside it, which had
    // loaded fine -- the exact failure the comment below describes, running the
    // other way. Catching it to null instead would put the row back in the
    // state a previous fix removed: zeros that look measured, on a row that
    // reports itself as loaded and never refetches.
    //
    // So it resolves to a marked failure. `retrieved: false` is what the
    // backend now sends when the lifetime read itself fails, and this reuses
    // that shape so a failed request and a failed query render identically --
    // there is nothing useful in telling a teacher which layer broke.
    apiFetch(`/api/stats/student/${studentId}`)
      .catch(err => { console.error('Failed to load student stats:', err); return { retrieved: false } }),
    // Caught here rather than left to reject the Promise.all: a signal-summary
    // outage should cost the four signal tiles, not the academic ones sitting
    // beside them that loaded fine.
    apiFetch(`/api/students/${studentId}/signal-summary?days=${SIGNAL_WINDOW_DAYS}`)
      .catch(err => { console.error('Failed to load signal summary:', err); return null }),
    supabase.from('user_math_performance')
      .select('topic_id, attempted_questions, correct_questions, math_topics(topic_name)')
      .eq('user_id', studentId)
  ])

  if (topicRes.error) console.error('Failed to load topic performance:', topicRes.error)

  const userStats = statsRes
  const signals = summary || {}
  // Three states, not two: read and empty, read and populated, or not read at
  // all. Only the last may not be rendered as a number -- "0 questions, 0%"
  // for a child whose record simply failed to load is the academic version of
  // reporting a quiet week that never happened.
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
    // Exact counts of non-null measurements over the window, from the
    // aggregate -- not a length that stops at a row cap.
    signalCount: signals.cognitive_samples ?? 0,
    faceSignalCount: signals.face_samples ?? 0,
    // Absolute units, deliberately not through asPct: bpm and ms are not
    // ratios, and running them through the percent helper is the single most
    // likely way this gets broken later.
    heartRate: typeof signals.heart_rate_bpm === 'number' ? Math.round(signals.heart_rate_bpm) : null,
    rmssd: typeof signals.rmssd_ms === 'number' ? Math.round(signals.rmssd_ms) : null,
    heartSamples: signals.heart_samples ?? 0,
    // From the payload, like faceIncluded below: the server decides this from
    // stored consent, and "the sensor is off" is a different statement from
    // "nothing was recorded".
    heartIncluded: signals.heart_included === true,
    // Whether those counts mean anything. The catch above deliberately keeps a
    // signal-summary outage from costing the academic tiles, which leaves every
    // signal figure at its zero default -- indistinguishable from a student who
    // recorded nothing unless the failure is carried alongside them. Without
    // it the copy below told a teacher a student had completed no sessions on
    // the strength of a request that never returned.
    //
    // Two ways to not have the data, and the tiles cannot tell them apart:
    // summary === null is the request itself failing, and retrieved === false
    // is the endpoint answering 200 with defaults because the aggregate query
    // behind it failed. The backend swallows that one so a broken read does not
    // blank a page, which is right -- but it means a successful-looking
    // response can still be carrying nothing, and only the flag says so.
    signalsFailed: summary === null || signals.retrieved === false,
    // From the payload, not from a local switch: the server decides this from
    // stored consent, and it is the difference between "the student turned the
    // camera off" and "the camera recorded nothing this week".
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
        // null, not 0: an untouched topic is not a topic scored zero, and
        // the bar below renders the two differently.
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
  // Same switch, same stored preference, as the student progress report. This
  // page reads face_signals too, and honouring the control on one surface while
  // ignoring it here would make it meaningless.
  const [hideSensors, setHideSensors] = useState(readHideSensorData)
  // Identifies the fetch whose result is still wanted, per student. Keying the
  // staleness check on the facial setting alone was not enough: flipping the
  // switch off and back on leaves two requests in flight that both carry
  // faceIncluded=true, so an older one landing last could overwrite newer data.
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
    // A cached row whose academic read failed is not a loaded row -- it is a
    // row holding signal tiles and a "couldn't be loaded" where the questions
    // go. Treating it as done is what made the earlier version of this stick:
    // the failure was cached, and collapsing and re-expanding never retried.
    //
    // So the entry is still cached (the signal tiles beside it loaded fine and
    // must survive), and re-expanding refetches it.
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
      // A throw here would otherwise leave the row's loading flag set forever,
      // and toggleExpand treats a loading student as already handled -- so the
      // row would show a spinner that collapsing and re-expanding never clears.
      // Same stuck-row failure the supersede path below is careful to avoid.
      console.error('Failed to load student stats:', err)
      if (requestId === statsRequestIds.current[studentId]) {
        setStatsLoading(prev => ({ ...prev, [studentId]: false }))
      }
      return
    }
    // Discard a superseded result. Without this a request that was still in
    // flight when the switch was turned off lands afterwards and puts facial
    // data back on screen -- and two requests under the same setting can still
    // land out of order, leaving the older one's data on display.
    if (requestId !== statsRequestIds.current[studentId]) return
    // Cleared only by the newest request: a superseded one doing it would stop
    // the indicator for the fetch still running, leaving the row showing
    // neither a spinner nor data until the replacement lands.
    setStatsLoading(prev => ({ ...prev, [studentId]: false }))
    setStatsCache(prev => ({ ...prev, [studentId]: stats }))
  }

  function handleHideSensorsChange(next) {
    setHideSensors(next)
    writeHideSensorData(next)
    // No cache drop and no re-read, unlike the control this replaces. That one
    // changed what the server returned, so every cached row and in-flight
    // request had to be superseded. This one changes only what is drawn, which
    // is the whole point of keeping it client-side.
  }


  return (
    <div className="p-6 lg:p-8 pb-12">
      <motion.div initial={{ opacity: 0, y: -10 }} animate={{ opacity: 1, y: 0 }} className="mb-6">
        <h1 className="text-3xl font-black text-gray-900 dark:text-white flex items-center gap-3">
          <Users className="text-violet-600" size={28} /> Students
        </h1>
        {/* Not "on the platform". The query scopes to classes this teacher
            owns, so that claimed a reach the page does not have -- and on a
            page about who a teacher may look at, overstating the scope is the
            wrong direction to be wrong in. */}
        <p className="text-gray-500 dark:text-gray-400 mt-1">Students enrolled in your classes.</p>
      </motion.div>

      {/* search */}
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
          {/* The setup note that used to sit here -- "requires a `profiles`
              table with a `role` column in Supabase" -- was a message to
              whoever deploys this, shown to every teacher who has no students
              yet. It named a schema they cannot see and cannot act on, in
              place of the one thing they can do about it. */}
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

                            {/* Gated by the teacher's "Hide sensor data" filter
                                (lib/viewPrefs.js) -- a display preference, not a
                                privacy control, so it hides these tiles without
                                changing what was fetched. */}
                            {!hideSensors && (
                            <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mt-3">
                              <MiniStat
                                icon={<Brain size={16} />}
                                label="Engagement"
                                value={stats.engagement ?? '—'}
                                sub={eegSub(stats.signalCount, stats.signalsFailed)}
                                color="indigo"
                              />
                              {/* "Off" rather than "—": the viewer switched
                                  facial reporting off, which is a different
                                  statement from having no reading.
                                  'reporting off' also wins over the failure
                                  note below it -- with the switch off no facial
                                  data was requested, so a summary outage did
                                  not cost these two tiles anything. */}
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

                            {/* Heart in its own row and its own units. Only
                                when the channel was read: a row of "—" is
                                indistinguishable from a headband that recorded
                                nothing, and "Off" says which. Also gated by
                                "Hide sensor data" -- see above. */}
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

                            {/* Both branches assert an absence, so both are
                                gated on having actually looked.

                                The facial clause only applies when facial data
                                was read. With the switch off faceSignalCount is
                                0 by construction, so including it
                                unconditionally let this claim "no sessions" for
                                a student whose only recorded activity was the
                                facial signals we were asked not to look at.

                                signalsFailed is the same mistake reached the
                                other way: every signal count is 0 when the
                                summary request failed, so the unguarded check
                                told a teacher a student had completed nothing
                                on the strength of a request that never
                                returned. There is nothing to assert in that
                                case -- only the outage to report, which is
                                worth saying rather than leaving the tiles to
                                explain on their own. */}
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