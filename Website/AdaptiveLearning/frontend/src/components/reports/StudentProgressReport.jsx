import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { motion } from 'framer-motion'
import { ArrowLeft, BookOpen, Target, Flame, TrendingUp } from 'lucide-react'
import { WeeklySignalReport, LiveSignalSummary, FacialRecognitionToggle, StrategyPanel } from '../signals/SignalPanel'
import { apiFetch } from '../../lib/api'
// Persisted so the choice survives navigation between students -- switching
// facial reporting off and having it silently come back on the next page is
// the kind of thing that makes a privacy control untrustworthy. Shared with the
// teacher student list, which reads the same facial signals.
import { readFacePref, writeFacePref } from '../../lib/facePref'

const TOPIC_ICONS = { ordering:'🔢', rationals:'➗', expressions:'📐', algebra:'🔣', geometry:'📏', angle_relationships:'📐', mean:'〰️', median:'📊', mode:'🔁', probability:'🎲' }

/**
 * A single student's full learning report: academic stat cards, the weekly
 * EEG/face signal panels, topic performance, and recent sessions.
 *
 * Shared by the parent-facing ChildDetail page and the teacher-facing
 * StudentReport page, which differ only in navigation chrome, one copy string,
 * and how the display name is resolved.
 *
 * @param {string}   studentId       user_id whose report to load.
 * @param {string}   initialName     name shown until a better one resolves.
 * @param {string}   backTo          route for the "back" link.
 * @param {string}   backLabel       text for the "back" link.
 * @param {string}   backHoverClass  Tailwind hover colour for the "back" link.
 * @param {string}   emptyTopicText  shown when the student has no topic data yet.
 * @param {Function} [nameFetch]     optional independent name source returning a
 *   Promise<string|null>. When provided it owns the name (the parent's children
 *   list, which survives a weekly-report failure); when omitted the name comes
 *   from the weekly-report's student_name.
 * @param {boolean}  [showStrategies] render the at-home strategies panel. Parent
 *   route only -- the copy is written for someone supporting a child at home.
 */
export default function StudentProgressReport({
  studentId,
  initialName = 'Student',
  backTo,
  backLabel,
  backHoverClass = 'hover:text-violet-600',
  emptyTopicText = 'No topic data yet.',
  nameFetch,
  showStrategies = false,
}) {
  const [stats, setStats]         = useState(null)
  const [sessions, setSessions]   = useState([])
  const [perf, setPerf]           = useState([])
  const [loading, setLoading]     = useState(true)
  const [name, setName]           = useState(initialName)
  const [signalReport, setSignalReport] = useState(null)
  const [signalError, setSignalError]   = useState(null)
  const [loadError, setLoadError]       = useState(null)
  const [includeFace, setIncludeFace]   = useState(readFacePref)
  const [strategies, setStrategies]     = useState(null)
  const [strategySource, setStrategySource]   = useState(null)
  // Whether the aggregate the advice was derived from actually loaded.
  // The endpoint answers either way -- the rules read a null average as no
  // signal to act on and fall through to their generic branches -- so
  // without this the panel presents a generic list as one built from the
  // child's week. Tracked beside `source` because it qualifies the result
  // the same way.
  const [strategySignals, setStrategySignals] = useState(null)
  const [strategyError, setStrategyError]     = useState(null)
  const [strategyLoading, setStrategyLoading] = useState(false)
  // Identifies the generation request whose result is still wanted. Bumped by
  // both a new generation and the facial toggle, so a response that lands after
  // either cannot overwrite what replaced it.
  const strategyRequestId = useRef(0)

  // Academic stats and the name. Deliberately not re-run when the facial
  // toggle flips: none of this depends on it, and re-fetching three endpoints
  // to change one query parameter is the regression #36 shipped.
  useEffect(() => {
    Promise.all([
      apiFetch(`/api/stats/student/${studentId}`),
      apiFetch(`/api/sessions/student/${studentId}`),
      apiFetch(`/api/performance/student/${studentId}`),
    ]).then(([s, sess, p]) => {
      setStats(s)
      setSessions(sess || [])
      setPerf(p || [])
      setLoadError(null)
      setLoading(false)
    }).catch(err => {
      // A failed core load (a 403 for a student outside the teacher's classes,
      // offline, or a server error) must not fall through to the zeros-filled
      // report below: that reads as a real-but-inactive student and, on the
      // teacher route, hides that the id simply isn't theirs to view. Surface it.
      setLoadError(err.message || 'Could not load this report')
      setLoading(false)
    })

    // Optional independent name source (the parent's children list). Kept apart
    // from the weekly-report below so the heading still shows the real name when
    // that heavier request fails.
    if (nameFetch) {
      nameFetch().then(n => { if (n) setName(n) }).catch(() => {})
    }
  }, [studentId, nameFetch])

  // The signal report, which is the only thing the facial toggle changes.
  // Fetched separately from the Promise.all above: this is the heaviest query,
  // and a failure here shouldn't blank the page when the stats loaded fine.
  //
  // nameFetch is in the dependency list because the body reads it. Callers
  // memoise it against the same id, so it changes only when studentId does --
  // it does not cause an extra fetch on toggle.
  useEffect(() => {
    let cancelled = false
    apiFetch(`/api/students/${studentId}/weekly-report?include_face=${includeFace}`)
      .then(r => {
        if (cancelled) return
        setSignalReport(r)
        setSignalError(null)
        // With no independent nameFetch (the teacher case), the report is the
        // name source; seed the heading from it once it arrives.
        if (!nameFetch && r?.student_name) setName(r.student_name)
      })
      // Tracked separately from "no report": a failed request renders
      // identically to a quiet week otherwise, and telling a viewer the student
      // had no activity when the request just failed is worse than saying
      // nothing. Same distinction #16 established for ClassDetail.
      .catch(err => {
        if (cancelled) return
        setSignalReport(null)
        setSignalError(err.message || 'Could not load signal report')
      })
    // Toggling twice quickly can land the responses out of order, and the
    // report carries the face data the toggle is meant to exclude -- so a
    // stale resolve could put it back on screen after it was switched off.
    return () => { cancelled = true }
  }, [studentId, includeFace, nameFetch])

  function handleIncludeFaceChange(next) {
    setIncludeFace(next)
    writeFacePref(next)

    // Drop the facial values from the report already on screen rather than
    // leaving them up for the round-trip. The switch governs what gets read,
    // but a viewer who has just asked to exclude facial data should not go on
    // looking at it while the replacement request is in flight -- which is what
    // happened here: the panels kept rendering the previous payload, whose
    // face_included is true, so Face Attention, Dominant Emotion and the
    // attention series stayed on screen with the switch already reading "off".
    //
    // face_included goes false in both directions, including when the switch is
    // being turned back on, because the flag describes the payload in hand and
    // this one no longer carries facial data. The panels therefore read "Off"
    // until the response lands, rather than "N/A" -- which would report a
    // measurement as missing when it is simply on its way. Same treatment the
    // parent dashboard gives its own tiles.
    //
    // The per-day face_retrieved flags go to null, not false: null is "not
    // requested", and leaving a cap-driven false behind would go on blaming a
    // retrieval gap on facial data this report no longer shows.
    //
    // summary goes too, and it is the one field here that is not a number. The
    // backend joins its clauses into a finished sentence, so a report that read
    // facial data carries "average face attention was 72%" inside prose there is
    // no structure left to edit -- and it renders verbatim. Nulling the numeric
    // fields around it left that sentence up as the last facial measurement on
    // screen, directly above the "facial recognition data was not included"
    // note, each contradicting the other. The panel falls back to "No summary
    // available yet." for the round-trip, which costs the still-valid EEG
    // clauses -- the same trade the tiles above already make, and the
    // replacement sentence is already in flight.
    setSignalReport(prev => prev && {
      ...prev,
      face_included: false,
      summary: null,
      averages:   { ...(prev.averages   || {}), face_attention: null },
      highlights: { ...(prev.highlights || {}), dominant_emotion: null },
      latest:     { ...(prev.latest     || {}), face: null },
      daily: (prev.daily || []).map(d => ({ ...d, attention: null, face_retrieved: null })),
    })

    // Built from a report that included facial data; keep it out of the way
    // rather than leaving advice on screen that the new setting excludes.
    //
    // Bumping the request id matters as much as clearing the state: a
    // generation already in flight was built from a report that read facial
    // data, and letting it resolve would put that advice back on screen after
    // the viewer switched it off.
    strategyRequestId.current += 1
    setStrategies(null)
    setStrategySource(null)
    setStrategySignals(null)
    setStrategyError(null)
    setStrategyLoading(false)
  }

  async function generateStrategies() {
    const requestId = ++strategyRequestId.current
    setStrategyLoading(true)
    setStrategyError(null)
    try {
      const res = await apiFetch(`/api/students/${studentId}/learning-strategies`, {
        method: 'POST',
        body: { include_face: includeFace },
      })
      if (requestId !== strategyRequestId.current) return
      setStrategies(res.strategies || [])
      setStrategySource(res.source || null)
      // Absent on payloads predating the field, which came from a working
      // read by definition -- null leaves the panel's default claim intact.
      setStrategySignals(res.basis?.signals_retrieved ?? null)
    } catch (err) {
      if (requestId !== strategyRequestId.current) return
      setStrategies(null)
      setStrategySource(null)
      setStrategySignals(null)
      setStrategyError(err.message || 'Could not generate strategies right now.')
    } finally {
      // Only the newest request owns the spinner; a superseded one clearing it
      // would stop the indicator for the generation still running.
      if (requestId === strategyRequestId.current) setStrategyLoading(false)
    }
  }

  const acc = stats?.total_questions > 0 ? Math.round((stats.total_correct / stats.total_questions) * 100) : 0

  return (
    <div className="p-6 lg:p-8 pb-12">
      <motion.div initial={{ opacity: 0, y: -10 }} animate={{ opacity: 1, y: 0 }} className="mb-6">
        <Link to={backTo} className={`flex items-center gap-2 text-sm text-gray-500 dark:text-gray-400 ${backHoverClass} mb-3 transition font-semibold w-fit`}>
          <ArrowLeft size={16} /> {backLabel}
        </Link>
        <h1 className="text-3xl font-black text-gray-900 dark:text-white">{name}'s Progress</h1>
        <p className="text-gray-500 dark:text-gray-400 mt-1">Full learning report.</p>
      </motion.div>

      {loading ? (
        <div className="space-y-4">{[1,2,3].map(i => <div key={i} className="h-32 bg-white dark:bg-gray-900 rounded-2xl border border-gray-100 dark:border-gray-800 animate-pulse" />)}</div>
      ) : loadError ? (
        // A core-load failure shows an honest error here rather than the
        // zeros-filled report; for a 403 the message is the backend's own
        // "You do not have access to this student". The back link above stays,
        // so a teacher who mistyped a student id can still get out.
        <div className="rounded-2xl border border-gray-100 dark:border-gray-800 bg-white dark:bg-gray-900 p-8 shadow-sm text-center">
          <p className="text-sm font-semibold text-gray-600 dark:text-gray-300">Couldn&apos;t load this student&apos;s report.</p>
          <p className="text-xs text-gray-400 mt-1">{loadError}</p>
        </div>
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

          <FacialRecognitionToggle enabled={includeFace} onChange={handleIncludeFaceChange} />

          {/* Only rendered once the report loads -- the panels would otherwise
              show a full grid of "N/A" and read as "no activity" rather than
              "still loading". */}
          {signalError && (
            <div className="rounded-2xl border border-gray-100 dark:border-gray-800 bg-white dark:bg-gray-900 p-5 shadow-sm text-center">
              <p className="text-sm text-gray-500 dark:text-gray-400">Couldn&apos;t load the EEG &amp; face report.</p>
              <p className="text-xs text-gray-400 mt-1">{signalError}</p>
            </div>
          )}
          {signalReport && (
            <div className="grid lg:grid-cols-1 gap-6">
              <LiveSignalSummary report={signalReport} title="Latest Signal Snapshot" />
              <WeeklySignalReport report={signalReport} title="Weekly EEG & Face Report" />
            </div>
          )}

          {showStrategies && (
            <StrategyPanel
              strategies={strategies}
              source={strategySource}
              signalsRetrieved={strategySignals}
              loading={strategyLoading}
              error={strategyError}
              onGenerate={generateStrategies}
            />
          )}

          <div className="grid lg:grid-cols-2 gap-6">
            {/* topic performance */}
            <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ delay: 0.35 }}
              className="bg-white dark:bg-gray-900 rounded-2xl border border-gray-100 dark:border-gray-800 p-5 shadow-sm">
              <h3 className="font-black text-gray-900 dark:text-white mb-5">Topic Performance</h3>
              {perf.length === 0 ? (
                <p className="text-gray-400 text-sm text-center py-6">{emptyTopicText}</p>
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
