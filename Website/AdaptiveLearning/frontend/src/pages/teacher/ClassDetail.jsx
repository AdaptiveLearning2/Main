import { useEffect, useRef, useState } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { motion } from 'framer-motion'
import { ArrowLeft, Copy, Check, GraduationCap, Users } from 'lucide-react'
import { apiFetch } from '../../lib/api'
import { toast } from 'sonner'
import AlertFeed from '../../components/analytics/AlertFeed'
import ClassTopicHeatmap from '../../components/analytics/ClassTopicHeatmap'
import ClassAccuracyTrend from '../../components/analytics/ClassAccuracyTrend'
import ClassTimeOfDay from '../../components/analytics/ClassTimeOfDay'
import ClassSignalTrend from '../../components/analytics/ClassSignalTrend'
import ClassSignalRoster from '../../components/analytics/ClassSignalRoster'
import { readHideSensorData } from '../../lib/viewPrefs'

/** "2 hours ago", or null when there is no timestamp to describe.
 *
 * Null rather than a placeholder, so the caller can say "never" or "unknown"
 * itself — those are different facts and only it knows which applies.
 */
function agoLabel(iso) {
  if (!iso) return null
  const then = new Date(iso)
  if (Number.isNaN(then.getTime())) return null
  const mins = Math.round((Date.now() - then.getTime()) / 60000)
  if (mins < 1) return 'just now'
  if (mins < 60) return `${mins}m ago`
  if (mins < 60 * 24) return `${Math.round(mins / 60)}h ago`
  return `${Math.round(mins / 1440)}d ago`
}

export default function ClassDetail() {
  const { id } = useParams()
  const navigate = useNavigate()
  const [cls, setCls]           = useState(null)
  // Always an array, never null, so students.length can't crash the render.
  const [students, setStudents] = useState([])
  const [loading, setLoading]   = useState(true)
  const [error, setError]       = useState(null)
  const [copied, setCopied]     = useState(false)

  // The three analytics panels load independently of the roster and of each
  // other. Bundling them into `loadData`'s allSettled would mean one slow
  // aggregate holding up the student list, and one failed aggregate taking
  // the whole page to its error state — where the roster is the thing the
  // page is actually for.
  const [analytics, setAnalytics] = useState({})
  const [analyticsLoading, setAnalyticsLoading] = useState(true)
  // The teacher's own decluttering switch, not a privacy boundary — the data
  // is fetched either way. See `lib/viewPrefs.js`.
  const hideSensors = readHideSensorData()
  const analyticsRun = useRef(0)

  useEffect(() => { loadData() }, [id])
  useEffect(() => { loadAnalytics() }, [id])

  async function loadData() {
    setLoading(true)
    setError(null)
    try {
      // allSettled, not all: both requests can 404 for a missing class, and
      // we need both results before deciding what to tell the teacher.
      const [classRes, studentsRes] = await Promise.allSettled([
        apiFetch(`/api/classes/${id}`),
        apiFetch(`/api/classes/${id}/students`)
      ])

      // Only a 404 on the class request itself means "class not found".
      // Any other failure means the class exists but couldn't be shown.
      if (classRes.status === 'rejected') {
        if (classRes.reason?.status !== 404) throw classRes.reason
        setCls(null)
        return
      }
      if (studentsRes.status === 'rejected') throw studentsRes.reason

      setCls(classRes.value)
      setStudents(Array.isArray(studentsRes.value) ? studentsRes.value : [])
    } catch (err) {
      // Kept separate from "class not found" so a failed request doesn't
      // send the teacher looking for the wrong problem.
      setError(err.message || 'Could not load class')
      toast.error(err.message || 'Could not load class')
    } finally {
      setLoading(false)
    }
  }

  async function loadAnalytics() {
    const run = ++analyticsRun.current
    setAnalyticsLoading(true)
    // Three independent reads. A rejected one becomes `retrieved: false`, the
    // same flag the backend sets when an aggregate fails behind a 200 — so the
    // panel has one thing to check rather than two, and a network failure and
    // a database failure reach it identically. Neither is an empty chart.
    const paths = {
      alerts: `/api/classes/${id}/alerts?days=7`,
      heatmap: `/api/classes/${id}/topic-heatmap`,
      trend: `/api/classes/${id}/accuracy-trend?days=30`,
      timeOfDay: `/api/classes/${id}/time-of-day?days=30`,
      cohortSignals: `/api/classes/${id}/cohort-signals?days=30`,
    }
    const settled = await Promise.allSettled(
      Object.values(paths).map(p => apiFetch(p)))

    // A class switched away from mid-flight must not repaint under the new
    // class's heading. A generation counter rather than a cleanup flag,
    // because the effect is not the only caller — the retry button is, and a
    // retry is exactly when someone changes class rather than waiting.
    if (run !== analyticsRun.current) return

    const next = {}
    Object.keys(paths).forEach((key, i) => {
      const res = settled[i]
      next[key] = res.status === 'fulfilled' ? res.value : { retrieved: false }
    })
    setAnalytics(next)
    setAnalyticsLoading(false)
  }

  function copyCode() {
    // Guard so it doesn't copy the literal string "undefined".
    if (!cls?.join_code) {
      toast.error('This class has no join code yet')
      return
    }
    navigator.clipboard.writeText(cls.join_code)
    setCopied(true)
    toast.success(`Copied code: ${cls.join_code}`)
    setTimeout(() => setCopied(false), 2000)
  }

  if (loading) {
    return (
      <div className="p-6 lg:p-8 pb-12">
        <div className="h-8 w-40 bg-gray-100 dark:bg-gray-800 rounded-lg animate-pulse mb-6" />
        <div className="space-y-3">
          {[1,2,3].map(i => <div key={i} className="h-16 bg-white dark:bg-gray-900 rounded-2xl border border-gray-100 dark:border-gray-800 animate-pulse" />)}
        </div>
      </div>
    )
  }

  if (error) {
    return (
      <div className="p-6 lg:p-8 text-center">
        <p className="text-gray-500 dark:text-gray-400 mb-1">Couldn&apos;t load this class.</p>
        <p className="text-xs text-gray-600 mb-4 dark:text-gray-400">{error}</p>
        <div className="flex items-center justify-center gap-2">
          <button onClick={loadData} className="px-5 py-2.5 bg-violet-600 text-white rounded-xl font-bold text-sm">Try again</button>
          <button onClick={() => navigate('/teacher/classes')} className="px-5 py-2.5 bg-slate-100 dark:bg-gray-800 text-gray-700 dark:text-gray-300 rounded-xl font-bold text-sm">Back to Classes</button>
        </div>
      </div>
    )
  }

  if (!cls) {
    return (
      <div className="p-6 lg:p-8 text-center">
        <p className="text-gray-500 dark:text-gray-400 mb-4">Class not found.</p>
        <button onClick={() => navigate('/teacher/classes')} className="px-5 py-2.5 bg-violet-600 text-white rounded-xl font-bold text-sm">Back to Classes</button>
      </div>
    )
  }

  return (
    <div className="p-6 lg:p-8 pb-12">
      <button onClick={() => navigate('/teacher/classes')}
        className="flex items-center gap-2 text-sm font-bold text-gray-500 dark:text-gray-400 hover:text-violet-600 dark:hover:text-violet-400 transition mb-6">
        <ArrowLeft size={16} /> Back to Classes
      </button>

      <motion.div initial={{ opacity: 0, y: -10 }} animate={{ opacity: 1, y: 0 }}
        className="flex items-center justify-between mb-8 flex-wrap gap-4">
        <div className="flex items-center gap-4">
          <div className="w-14 h-14 bg-gradient-to-br from-violet-400 to-purple-500 rounded-xl flex items-center justify-center text-white font-black text-xl shadow">
            {(cls.name || '?')[0].toUpperCase()}
          </div>
          <div>
            <h1 className="text-2xl font-black text-gray-900 dark:text-white">{cls.name || 'Untitled class'}</h1>
            <div className="flex items-center gap-2 mt-1 flex-wrap">
              <span className="text-xs text-gray-500 dark:text-gray-400">Code:</span>
              <span className="font-mono font-black text-violet-600 dark:text-violet-400 text-sm tracking-widest">{cls.join_code}</span>
              <button onClick={copyCode} className="p-1 rounded-md hover:bg-violet-50 dark:hover:bg-violet-900/30 transition">
                {copied ? <Check size={13} className="text-green-500" /> : <Copy size={13} className="text-gray-600 dark:text-gray-400" />}
              </button>
              <span className="flex items-center gap-1 ml-2 text-xs font-bold px-2.5 py-1 bg-indigo-100 dark:bg-indigo-900/40 text-indigo-700 dark:text-indigo-300 rounded-full">
                <GraduationCap size={11} /> {cls.grade_level || 'Grade not set'}
              </span>
            </div>
          </div>
        </div>
      </motion.div>

      <div className="flex items-center gap-2 mb-4">
        <Users size={18} className="text-violet-600" />
        <h2 className="font-black text-gray-900 dark:text-white">Students ({students?.length || 0})</h2>
      </div>

      {students.length === 0 ? (
        <div className="text-center py-16 bg-white dark:bg-gray-900 rounded-2xl border border-gray-100 dark:border-gray-800">
          <p className="text-gray-600 text-sm dark:text-gray-400">No students have joined yet. Share the code <span className="font-mono font-black text-violet-600">{cls.join_code}</span></p>
        </div>
      ) : (
        <div className="grid sm:grid-cols-2 lg:grid-cols-4 gap-3">
          {students.map((s, i) => {
            // Three states, never two. A timestamp, a student who has genuinely
            // never worked, and a read that failed. Collapsing the last two
            // tells a teacher nobody is working, which is both wrong and
            // something they would act on.
            const ago = agoLabel(s.last_active)
            const lastActive = s.last_active_retrieved === false
              ? 'Last active unknown'
              : ago ? `Active ${ago}` : 'Never active'
            return (
              <motion.div key={s.user_id}
                initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: i * 0.03 }}
                className="bg-white dark:bg-gray-900 rounded-xl border border-gray-100 dark:border-gray-800 p-4 flex items-center justify-between gap-3 shadow-sm">
                <div className="min-w-0">
                  <p className="text-lg font-bold text-gray-900 dark:text-white truncate">{s.name}</p>
                  <p className="text-xs text-gray-600 dark:text-gray-400">{lastActive}</p>
                </div>
                <button onClick={() => navigate(`/teacher/students/${s.user_id}/report`, { state: { name: s.name, classId: id, className: cls?.name } })}
                  className="shrink-0 bg-violet-600 hover:bg-violet-700 text-white rounded-xl px-4 py-2 text-sm font-bold transition">
                  Get Report
                </button>
              </motion.div>
            )
          })}
        </div>
      )}

      <div className="mt-8 grid lg:grid-cols-2 gap-6">
        {/* First, because it is the only panel that asks for an action. */}
        <AlertFeed data={analytics.alerts} loading={analyticsLoading}
          onRetry={loadAnalytics} />
        <ClassAccuracyTrend data={analytics.trend} loading={analyticsLoading}
          onRetry={loadAnalytics} />
        <ClassTopicHeatmap data={analytics.heatmap} loading={analyticsLoading}
          onRetry={loadAnalytics} />
        <ClassTimeOfDay data={analytics.timeOfDay} loading={analyticsLoading}
          onRetry={loadAnalytics} />
        {/* Sensor surfaces, so they honour the teacher's "Hide sensor data"
            switch — a reporting view like the weekly report, not a live
            supervision one. Read once per render rather than held in state:
            it is a localStorage flag with no event to subscribe to, and the
            page re-renders whenever the analytics land anyway. */}
        <ClassSignalTrend data={analytics.cohortSignals} loading={analyticsLoading}
          onRetry={loadAnalytics} hideSensors={hideSensors} />
        <ClassSignalRoster data={analytics.cohortSignals} loading={analyticsLoading}
          onRetry={loadAnalytics} hideSensors={hideSensors} />
      </div>
    </div>
  )
}
