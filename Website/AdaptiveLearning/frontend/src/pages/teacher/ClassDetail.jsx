import { useEffect, useState } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { motion } from 'framer-motion'
import { ArrowLeft, Copy, Check, GraduationCap, Users } from 'lucide-react'
import { apiFetch } from '../../lib/api'
import { toast } from 'sonner'

export default function ClassDetail() {
  const { id } = useParams()
  const navigate = useNavigate()
  const [cls, setCls]           = useState(null)
  // Always an array, never null, so students.length can't crash the render.
  const [students, setStudents] = useState([])
  const [loading, setLoading]   = useState(true)
  const [error, setError]       = useState(null)
  const [copied, setCopied]     = useState(false)

  useEffect(() => { loadData() }, [id])

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
        <p className="text-xs text-gray-400 mb-4">{error}</p>
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
                {copied ? <Check size={13} className="text-green-500" /> : <Copy size={13} className="text-gray-400" />}
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
          <p className="text-gray-400 text-sm">No students have joined yet. Share the code <span className="font-mono font-black text-violet-600">{cls.join_code}</span></p>
        </div>
      ) : (
        <div className="grid sm:grid-cols-2 lg:grid-cols-4 gap-3">
          {students.map((s, i) => {
            return (
              <motion.div key={s.user_id}
                initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: i * 0.03 }}
                className="bg-white dark:bg-gray-900 rounded-xl border border-gray-100 dark:border-gray-800 p-4 flex items-center justify-center gap-3 shadow-sm">
               
                <p className="min-w-0 text-lg font-bold text-gray-900 dark:text-white truncate">{s.name}</p>
                <button onClick={() => navigate(`/teacher/students/${s.user_id}/report`, { state: { name: s.name, classId: id, className: cls?.name } })}
                  className="shrink-0 bg-violet-600 hover:bg-violet-700 text-white rounded-xl px-4 py-2 text-sm font-bold transition">
                  Get Report
                </button>
              </motion.div>
            )
          })}
        </div>
      )}
    </div>
  )
}
