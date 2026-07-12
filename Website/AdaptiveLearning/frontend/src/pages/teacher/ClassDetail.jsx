import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { motion, AnimatePresence } from 'framer-motion'
import { ArrowLeft, Users, Copy, FileText, Target, Brain, Zap, Eye } from 'lucide-react'
import { toast } from 'sonner'
import { apiFetch } from '../../lib/api'
import { WeeklySignalReport } from '../../components/signals/SignalPanel'

function pct(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return 'N/A'
  return `${Math.round(Number(value))}%`
}

export default function ClassDetail() {
  const { id } = useParams()
  const [cls, setCls] = useState(null)
  const [students, setStudents] = useState([])
  const [loading, setLoading] = useState(true)
  const [openReport, setOpenReport] = useState(null)
  const [reportLoading, setReportLoading] = useState(null)
  const [reports, setReports] = useState({})

  useEffect(() => {
    Promise.all([
      apiFetch(`/api/classes/${id}`),
      apiFetch(`/api/classes/${id}/students`),
    ]).then(([classData, studentData]) => {
      setCls(classData)
      setStudents(studentData || [])
    }).catch(err => {
      console.error(err)
      toast.error('Could not load class details')
    }).finally(() => setLoading(false))
  }, [id])

  function copyCode() {
    if (!cls?.join_code) return
    navigator.clipboard.writeText(cls.join_code)
    toast.success(`Copied code: ${cls.join_code}`)
  }

  async function toggleReport(studentId) {
    if (openReport === studentId) {
      setOpenReport(null)
      return
    }
    setOpenReport(studentId)
    if (reports[studentId]) return
    setReportLoading(studentId)
    try {
      const report = await apiFetch(`/api/teacher/students/${studentId}/weekly-eeg-report`)
      setReports(prev => ({ ...prev, [studentId]: report }))
    } catch (err) {
      toast.error(err.message || 'Could not load EEG report')
    } finally {
      setReportLoading(null)
    }
  }

  if (loading) {
    return <div className="p-6 lg:p-8"><div className="h-40 rounded-2xl bg-white dark:bg-gray-900 border border-gray-100 dark:border-gray-800 animate-pulse" /></div>
  }

  return (
    <div className="p-6 lg:p-8 pb-12">
      <motion.div initial={{ opacity: 0, y: -10 }} animate={{ opacity: 1, y: 0 }} className="mb-6">
        <Link to="/teacher/classes" className="flex items-center gap-2 text-sm text-gray-500 dark:text-gray-400 hover:text-violet-600 mb-3 transition font-semibold w-fit">
          <ArrowLeft size={16} /> Back to Classes
        </Link>
        <div className="rounded-3xl border border-gray-100 dark:border-gray-800 bg-white dark:bg-gray-900 p-6 shadow-sm">
          <div className="flex flex-wrap items-start justify-between gap-4">
            <div>
              <h1 className="text-3xl font-black text-gray-900 dark:text-white flex items-center gap-3">
                <Users className="text-violet-600" size={28} /> {cls?.name || 'Class'}
              </h1>
              <p className="text-gray-500 dark:text-gray-400 mt-1">Students enrolled in this class, with weekly EEG report access.</p>
            </div>
            <div className="flex flex-wrap gap-2">
              <button onClick={copyCode} className="inline-flex items-center gap-2 px-4 py-2 rounded-xl bg-slate-100 dark:bg-gray-800 text-gray-700 dark:text-gray-300 font-bold text-sm">
                <Copy size={14} /> {cls?.join_code || 'No Code'}
              </button>
              <span className="px-4 py-2 rounded-xl bg-indigo-100 dark:bg-indigo-900/40 text-indigo-700 dark:text-indigo-300 font-bold text-sm">{cls?.grade_level || 'Grade not set'}</span>
            </div>
          </div>
        </div>
      </motion.div>

      {students.length === 0 ? (
        <div className="text-center py-16 bg-white dark:bg-gray-900 rounded-2xl border border-gray-100 dark:border-gray-800">
          <div className="text-6xl mb-4">🏫</div>
          <h3 className="text-xl font-black text-gray-900 dark:text-white mb-2">No students yet</h3>
          <p className="text-gray-500 dark:text-gray-400 text-sm">Share the join code with students so they can join this class.</p>
        </div>
      ) : (
        <div className="grid lg:grid-cols-2 gap-5">
          {students.map((s, i) => {
            const report = reports[s.user_id] || s.weekly_report
            return (
              <motion.div key={s.user_id} initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: i * 0.04 }}
                className="bg-white dark:bg-gray-900 rounded-2xl border border-gray-100 dark:border-gray-800 shadow-sm overflow-hidden">
                <div className="p-5">
                  <div className="flex items-start justify-between gap-4">
                    <div className="flex items-center gap-3 min-w-0">
                      <div className="w-11 h-11 rounded-full bg-gradient-to-br from-violet-400 to-purple-500 flex items-center justify-center text-white font-black flex-shrink-0">
                        {(s.name || 'S')[0].toUpperCase()}
                      </div>
                      <div className="min-w-0">
                        <h3 className="font-black text-gray-900 dark:text-white truncate">{s.name}</h3>
                        <p className="text-xs text-gray-400 truncate">{s.email || 'No email'}</p>
                      </div>
                    </div>
                    <button onClick={() => toggleReport(s.user_id)} className="inline-flex items-center gap-2 px-3 py-2 rounded-xl bg-violet-600 text-white text-xs font-bold hover:bg-violet-700 transition">
                      <FileText size={14} /> {openReport === s.user_id ? 'Hide Report' : 'Get Report'}
                    </button>
                  </div>

                  <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mt-5">
                    <div className="rounded-xl bg-slate-50 dark:bg-gray-800 p-3"><Target size={15} className="text-violet-500 mb-1" /><p className="text-xs text-gray-400">Accuracy</p><p className="font-black dark:text-white">{pct(s.accuracy)}</p></div>
                    <div className="rounded-xl bg-emerald-50 dark:bg-emerald-900/20 p-3"><Brain size={15} className="text-emerald-500 mb-1" /><p className="text-xs text-gray-400">Focus</p><p className="font-black text-emerald-600">{pct(s.weekly_report?.averages?.focus)}</p></div>
                    <div className="rounded-xl bg-rose-50 dark:bg-rose-900/20 p-3"><Zap size={15} className="text-rose-500 mb-1" /><p className="text-xs text-gray-400">Stress</p><p className="font-black text-rose-600">{pct(s.weekly_report?.averages?.stress)}</p></div>
                    <div className="rounded-xl bg-sky-50 dark:bg-sky-900/20 p-3"><Eye size={15} className="text-sky-500 mb-1" /><p className="text-xs text-gray-400">Face</p><p className="font-black text-sky-600">{pct(s.weekly_report?.averages?.face_attention)}</p></div>
                  </div>

                  {s.topic_breakdown?.length > 0 && (
                    <div className="mt-5">
                      <p className="text-xs font-bold uppercase tracking-widest text-gray-400 mb-3">Topic Scores</p>
                      <div className="grid sm:grid-cols-2 gap-2">
                        {s.topic_breakdown.slice(0, 6).map(t => (
                          <div key={t.topic_id || t.topic_name} className="rounded-xl bg-slate-50 dark:bg-gray-800 p-3">
                            <div className="flex justify-between gap-2 text-xs mb-1"><span className="capitalize text-gray-500 dark:text-gray-400">{String(t.topic_name).replace('_', ' ')}</span><span className="font-black dark:text-white">{pct(t.accuracy)}</span></div>
                            <div className="h-1.5 bg-gray-200 dark:bg-gray-700 rounded-full overflow-hidden"><div className="h-full bg-violet-500" style={{ width: `${t.accuracy || 0}%` }} /></div>
                          </div>
                        ))}
                      </div>
                    </div>
                  )}
                </div>

                <AnimatePresence>
                  {openReport === s.user_id && (
                    <motion.div initial={{ height: 0, opacity: 0 }} animate={{ height: 'auto', opacity: 1 }} exit={{ height: 0, opacity: 0 }} className="overflow-hidden border-t border-gray-100 dark:border-gray-800">
                      <div className="p-5 bg-slate-50 dark:bg-gray-950">
                        {reportLoading === s.user_id ? <div className="h-48 rounded-2xl bg-white dark:bg-gray-900 animate-pulse" /> : <WeeklySignalReport report={report} title={`${s.name}'s Weekly EEG Report`} />}
                      </div>
                    </motion.div>
                  )}
                </AnimatePresence>
              </motion.div>
            )
          })}
        </div>
      )}
    </div>
  )
}
