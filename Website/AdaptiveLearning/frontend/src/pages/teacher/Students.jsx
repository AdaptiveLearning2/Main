import { useEffect, useRef, useState } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { supabase } from '../../lib/supabase'
import { Users, Search, ChevronDown, Eye, Flame, Brain, Smile, Target, TrendingUp, Zap} from 'lucide-react'
import { FacialRecognitionToggle } from '../../components/signals/SignalPanel'
import { readFacePref, writeFacePref } from '../../lib/facePref'

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
  const [includeFace, setIncludeFace] = useState(readFacePref)
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
      console.log('raw result:', data)

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
    if (statsCache[studentId] || statsLoading[studentId]) return
    await refreshStats(studentId, includeFace)
  }

  async function refreshStats(studentId, withFace) {
    const requestId = (statsRequestIds.current[studentId] || 0) + 1
    statsRequestIds.current[studentId] = requestId
    setStatsLoading(prev => ({ ...prev, [studentId]: true }))
    const stats = await getStudentStats(studentId, withFace)
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

  function handleIncludeFaceChange(next) {
    setIncludeFace(next)
    writeFacePref(next)
    // Cached rows were read under the previous setting -- with facial data in
    // them, or without. Drop the cache so what is on screen matches the switch
    // rather than whatever happened to be fetched first.
    setStatsCache({})
    if (expandedId) refreshStats(expandedId, next)
  }

  // Signals are stored as 0..1 ratios in cognitive_signals and face_signals,
  // which is the interpretation the original placeholders were waiting on. The
  // rest of the app scales them by 100 at render (Live.jsx's Gauge,
  // SignalPanel's pct), so these do too.
  //
  // Nulls are dropped before the conversion, not after: every signal column is
  // nullable, and Number(null) is 0 -- which Number.isFinite happily accepts,
  // pulling the average down and turning a student with no usable readings into
  // a confident "0%" rather than "no data".
  const avg = (rows, key) => {
    const vals = rows
      .map(r => r[key])
      .filter(v => v !== null && v !== undefined)
      .map(Number)
      .filter(Number.isFinite)
    if (!vals.length) return null
    return vals.reduce((a, b) => a + b, 0) / vals.length
  }

  const asPct = (ratio) => (ratio === null ? null : `${Math.round(ratio * 100)}%`)

  // Function to retrieve student data from user_stats, cognitive_signals,
  // face_signals and topic performance in supabase.
  //
  // Read with the browser client, so RLS applies: the "cog: teacher read" and
  // "face: teacher read" policies scope these to students in a class the
  // signed-in teacher owns. A teacher outside that relationship gets zero rows
  // rather than an error.
  //
  // withFace=false skips the face_signals query outright rather than hiding the
  // result, matching what the weekly-report endpoint does for the same switch:
  // the point of the control is that the data is not read.
  async function getStudentStats(studentId, withFace = true)
  {
     const [statsRes, signalsRes, faceRes, topicRes] = await Promise.all([
      supabase.from('user_stats').select('*').eq('user_id', studentId).maybeSingle(),
      supabase.from('cognitive_signals')
        .select('focus, stress, engagement, ts')
        .eq('user_id', studentId)
        .order('ts', { ascending: false })
        .limit(200),
      withFace
        ? supabase.from('face_signals')
            .select('attention, emotion, ts')
            .eq('user_id', studentId)
            .order('ts', { ascending: false })
            .limit(200)
        : Promise.resolve({ data: [], error: null }),
      supabase.from('user_math_performance')
        .select('topic_id, attempted_questions, correct_questions, math_topics(topic_name)')
        .eq('user_id', studentId)
    ])

    if (statsRes.error) console.error('Failed to load user_stats:', statsRes.error)
    if (signalsRes.error) console.error('Failed to load cognitive_signals:', signalsRes.error)
    if (faceRes.error) console.error('Failed to load face_signals:', faceRes.error)
    if (topicRes.error) console.error('Failed to load topic performance:', topicRes.error)

    const userStats = statsRes.data
    const signals = signalsRes.data || []
    const faceSignals = faceRes.data || []

    const totalAccuracy = userStats && userStats.total_questions > 0
      ? Math.round((userStats.total_correct / userStats.total_questions) * 100)
      : null

    const emotionCounts = {}
    for (const f of faceSignals) {
      if (f.emotion) emotionCounts[f.emotion] = (emotionCounts[f.emotion] || 0) + 1
    }
    const dominantEmotion = Object.entries(emotionCounts)
      .sort((a, b) => b[1] - a[1])[0]?.[0] ?? null

    return {
      totalAccuracy,
      totalQuestions: userStats?.total_questions ?? 0,
      currentStreak: userStats?.current_streak ?? 0,
      bestStreak: userStats?.best_streak ?? 0,
      focusScore: asPct(avg(signals, 'focus')),
      stressLevel: asPct(avg(signals, 'stress')),
      engagement: asPct(avg(signals, 'engagement')),
      faceAttention: asPct(avg(faceSignals, 'attention')),
      dominantEmotion,
      signalCount: signals.length,
      faceSignalCount: faceSignals.length,
      // Lets the panel say "Off" rather than "—": a switched-off control is a
      // different statement from a student with no facial readings.
      faceIncluded: withFace,
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
  return (
    <div className="p-6 lg:p-8 pb-12">
      <motion.div initial={{ opacity: 0, y: -10 }} animate={{ opacity: 1, y: 0 }} className="mb-6">
        <h1 className="text-3xl font-black text-gray-900 dark:text-white flex items-center gap-3">
          <Users className="text-violet-600" size={28} /> Students
        </h1>
        <p className="text-gray-500 dark:text-gray-400 mt-1">All enrolled students on the platform.</p>
      </motion.div>

      {/* search */}
      <div className="relative mb-6 max-w-sm">
        <Search size={16} className="absolute left-3.5 top-1/2 -translate-y-1/2 text-gray-400" />
        <input value={search} onChange={e => setSearch(e.target.value)}
          className="w-full pl-10 pr-4 py-2.5 bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-700 rounded-xl text-sm dark:text-white outline-none focus:ring-2 focus:ring-violet-500 transition"
          placeholder="Search students..." />
      </div>

      <div className="mb-6">
        <FacialRecognitionToggle enabled={includeFace} onChange={handleIncludeFaceChange} />
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
              ? 'Students will appear here once they sign up.'
              : 'Try a different search term.'}
          </p>
          <p className="text-xs text-gray-400 mt-2">Requires a <code className="bg-gray-100 dark:bg-gray-800 px-1 py-0.5 rounded">profiles</code> table with a <code className="bg-gray-100 dark:bg-gray-800 px-1 py-0.5 rounded">role</code> column in Supabase.</p>
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
                              <StatCard
                                icon={<TrendingUp size={16} />}
                                label="Total Accuracy"
                                value={stats.totalAccuracy !== null ? `${stats.totalAccuracy}%` : '—'}
                                sub={`${stats.totalQuestions} questions`}
                                color="indigo"
                              />
                              <StatCard
                                icon={<Flame size={16} />}
                                label="Stress Level"
                                value={stats.stressLevel ?? '—'}
                                sub={stats.signalCount ? `${stats.signalCount} EEG readings` : 'no EEG data'}
                                color="rose"
                              />
                              <StatCard
                                icon={<Target size={16} />}
                                label="Focus Score"
                                value={stats.focusScore ?? '—'}
                                sub={stats.signalCount ? `${stats.signalCount} EEG readings` : 'no EEG data'}
                                color="emerald"
                              />
                              <StatCard
                                icon={<Zap size={16} />}
                                label="Current Streak"
                                value={stats.currentStreak}
                                sub={`best: ${stats.bestStreak}`}
                                color="amber"
                              />
                            </div>

                            <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mt-3">
                              <StatCard
                                icon={<Brain size={16} />}
                                label="Engagement"
                                value={stats.engagement ?? '—'}
                                sub={stats.signalCount ? `${stats.signalCount} EEG readings` : 'no EEG data'}
                                color="indigo"
                              />
                              {/* "Off" rather than "—": the viewer switched
                                  facial reporting off, which is a different
                                  statement from having no reading. */}
                              <StatCard
                                icon={<Eye size={16} />}
                                label="Face Attention"
                                value={stats.faceIncluded ? (stats.faceAttention ?? '—') : 'Off'}
                                sub={stats.faceIncluded
                                  ? (stats.faceSignalCount ? `${stats.faceSignalCount} face readings` : 'no face data')
                                  : 'reporting off'}
                                color="sky"
                              />
                              <StatCard
                                icon={<Smile size={16} />}
                                label="Dominant Emotion"
                                value={stats.faceIncluded ? (stats.dominantEmotion ?? '—') : 'Off'}
                                sub={stats.faceIncluded
                                  ? (stats.faceSignalCount ? 'most frequent this window' : 'no face data')
                                  : 'reporting off'}
                                color="violet"
                              />
                            </div>

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

                            {stats.totalQuestions === 0 && stats.signalCount === 0 && stats.faceSignalCount === 0 && (
                              <p className="text-xs text-gray-400 mt-3">
                                This student hasn't completed any sessions yet.
                              </p>
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