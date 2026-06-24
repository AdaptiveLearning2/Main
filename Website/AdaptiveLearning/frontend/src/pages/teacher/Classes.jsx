import { useEffect, useState } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { Users, Plus, X, Copy, Check, GraduationCap, Pencil, Save } from 'lucide-react'
import { apiFetch } from '../../lib/api'
import { toast } from 'sonner'

const GRADES = ['1st Grade','2nd Grade','3rd Grade','4th Grade','5th Grade','6th Grade','7th Grade','8th Grade','Highschool','College']

export default function Classes() {
  const [classes, setClasses]     = useState([])
  const [loading, setLoading]     = useState(true)
  const [creating, setCreating]   = useState(false)
  const [newName, setNewName]     = useState('')
  const [newGrade, setNewGrade]   = useState('5th Grade')
  const [showForm, setShowForm]   = useState(false)
  const [copiedId, setCopiedId]   = useState(null)
  const [expanded, setExpanded]   = useState(null)
  const [students, setStudents]   = useState({})
  const [editingId, setEditingId] = useState(null)
  const [editGrade, setEditGrade] = useState('')

  useEffect(() => { loadClasses() }, [])

  async function loadClasses() {
    try { setClasses(await apiFetch('/api/classes')) } catch {}
    setLoading(false)
  }

  async function createClass(e) {
    e.preventDefault()
    if (!newName.trim()) return
    setCreating(true)
    try {
      const cls = await apiFetch('/api/classes', {
        method: 'POST',
        body: { name: newName.trim(), grade_level: newGrade }
      })
      setClasses(prev => [cls, ...prev])
      setNewName('')
      setShowForm(false)
      toast.success(`Class "${cls.name}" created! Code: ${cls.join_code}`)
    } catch (err) {
      toast.error(err.message || 'Failed to create class')
    } finally {
      setCreating(false)
    }
  }

  async function saveGrade(classId) {
    try {
      const updated = await apiFetch(`/api/classes/${classId}`, {
        method: 'PUT',
        body: { grade_level: editGrade }
      })
      setClasses(prev => prev.map(c => c.id === classId ? { ...c, ...updated } : c))
      setEditingId(null)
      toast.success('Grade updated')
    } catch (err) {
      toast.error(err.message || 'Failed to update')
    }
  }

  async function loadStudents(classId) {
    if (students[classId]) { setExpanded(classId); return }
    try {
      const s = await apiFetch(`/api/classes/${classId}/students`)
      setStudents(prev => ({ ...prev, [classId]: s }))
      setExpanded(classId)
    } catch { toast.error('Could not load students') }
  }

  function copyCode(code, id) {
    navigator.clipboard.writeText(code)
    setCopiedId(id)
    toast.success(`Copied code: ${code}`)
    setTimeout(() => setCopiedId(null), 2000)
  }

  return (
    <div className="p-6 lg:p-8 pb-12">
      <motion.div initial={{ opacity: 0, y: -10 }} animate={{ opacity: 1, y: 0 }} className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-3xl font-black text-gray-900 dark:text-white flex items-center gap-3">
            <Users className="text-violet-600" size={28} /> Classes
          </h1>
          <p className="text-gray-500 dark:text-gray-400 mt-1">Pick a grade level — the AI uses it for every student in this class.</p>
        </div>
        <motion.button onClick={() => setShowForm(s => !s)}
          whileHover={{ scale: 1.03 }} whileTap={{ scale: 0.97 }}
          className="flex items-center gap-2 px-5 py-2.5 bg-gradient-to-r from-violet-600 to-purple-600 text-white rounded-xl font-bold shadow text-sm">
          <Plus size={16} /> New Class
        </motion.button>
      </motion.div>

      <AnimatePresence>
        {showForm && (
          <motion.div initial={{ opacity: 0, y: -10 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -10 }}
            className="bg-white dark:bg-gray-900 rounded-2xl border border-violet-200 dark:border-violet-800 p-5 shadow-sm mb-6">
            <form onSubmit={createClass} className="grid sm:grid-cols-[1fr_180px_auto_auto] gap-3 items-center">
              <input value={newName} onChange={e => setNewName(e.target.value)}
                className="px-4 py-2.5 bg-gray-50 dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded-xl text-sm dark:text-white outline-none focus:ring-2 focus:ring-violet-500"
                placeholder='Class name, e.g. "Period 3 Math"' autoFocus required />
              <select value={newGrade} onChange={e => setNewGrade(e.target.value)}
                className="px-4 py-2.5 bg-gray-50 dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded-xl text-sm dark:text-white outline-none focus:ring-2 focus:ring-violet-500">
                {GRADES.map(g => <option key={g} value={g}>{g}</option>)}
              </select>
              <button type="submit" disabled={creating || !newName.trim()}
                className="px-6 py-2.5 bg-violet-600 hover:bg-violet-700 text-white rounded-xl font-bold text-sm disabled:opacity-50 transition shadow">
                {creating ? '...' : 'Create'}
              </button>
              <button type="button" onClick={() => setShowForm(false)} className="p-2.5 rounded-xl border border-gray-200 dark:border-gray-700 hover:bg-gray-100 dark:hover:bg-gray-800 transition">
                <X size={16} className="text-gray-500" />
              </button>
            </form>
          </motion.div>
        )}
      </AnimatePresence>

      {loading ? (
        <div className="space-y-3">{[1,2,3].map(i => <div key={i} className="h-20 bg-white dark:bg-gray-900 rounded-2xl border border-gray-100 dark:border-gray-800 animate-pulse" />)}</div>
      ) : classes.length === 0 ? (
        <div className="text-center py-16 bg-white dark:bg-gray-900 rounded-2xl border border-gray-100 dark:border-gray-800">
          <div className="text-6xl mb-4">🏫</div>
          <h3 className="text-xl font-black text-gray-900 dark:text-white mb-2">No classes yet</h3>
          <p className="text-gray-500 dark:text-gray-400 text-sm mb-4">Create a class and share the join code with your students.</p>
          <button onClick={() => setShowForm(true)} className="px-5 py-2.5 bg-violet-600 text-white rounded-xl font-bold text-sm">Create your first class</button>
        </div>
      ) : (
        <div className="space-y-4">
          {classes.map((cls, i) => (
            <motion.div key={cls.id}
              initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: i * 0.07 }}
              className="bg-white dark:bg-gray-900 rounded-2xl border border-gray-100 dark:border-gray-800 shadow-sm overflow-hidden">
              <div className="flex items-center justify-between p-5 flex-wrap gap-4">
                <div className="flex items-center gap-4">
                  <div className="w-12 h-12 bg-gradient-to-br from-violet-400 to-purple-500 rounded-xl flex items-center justify-center text-white font-black text-lg shadow">
                    {cls.name[0].toUpperCase()}
                  </div>
                  <div>
                    <h3 className="font-black text-gray-900 dark:text-white">{cls.name}</h3>
                    <div className="flex items-center gap-2 mt-1 flex-wrap">
                      <span className="text-xs text-gray-500 dark:text-gray-400">Code:</span>
                      <span className="font-mono font-black text-violet-600 dark:text-violet-400 text-sm tracking-widest">{cls.join_code}</span>
                      <button onClick={() => copyCode(cls.join_code, cls.id)}
                        className="p-1 rounded-md hover:bg-violet-50 dark:hover:bg-violet-900/30 transition">
                        {copiedId === cls.id ? <Check size={13} className="text-green-500" /> : <Copy size={13} className="text-gray-400" />}
                      </button>

                      {/* grade pill / editor */}
                      {editingId === cls.id ? (
                        <span className="flex items-center gap-1 ml-2">
                          <select value={editGrade} onChange={e => setEditGrade(e.target.value)}
                            className="px-2 py-1 text-xs rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 dark:text-white">
                            {GRADES.map(g => <option key={g} value={g}>{g}</option>)}
                          </select>
                          <button onClick={() => saveGrade(cls.id)} className="p-1 rounded-md text-emerald-500 hover:bg-emerald-50 dark:hover:bg-emerald-900/30">
                            <Save size={13} />
                          </button>
                          <button onClick={() => setEditingId(null)} className="p-1 rounded-md text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-800">
                            <X size={13} />
                          </button>
                        </span>
                      ) : (
                        <span className="flex items-center gap-1 ml-2 text-xs font-bold px-2.5 py-1 bg-indigo-100 dark:bg-indigo-900/40 text-indigo-700 dark:text-indigo-300 rounded-full">
                          <GraduationCap size={11} /> {cls.grade_level || 'Grade not set'}
                          <button onClick={() => { setEditingId(cls.id); setEditGrade(cls.grade_level || '5th Grade') }}
                            className="ml-1 opacity-60 hover:opacity-100"><Pencil size={11} /></button>
                        </span>
                      )}
                    </div>
                  </div>
                </div>
                <button onClick={() => expanded === cls.id ? setExpanded(null) : loadStudents(cls.id)}
                  className="px-4 py-2 bg-violet-50 dark:bg-violet-900/30 text-violet-700 dark:text-violet-300 rounded-xl text-sm font-bold hover:bg-violet-100 dark:hover:bg-violet-900/50 transition">
                  {expanded === cls.id ? 'Hide Students' : 'View Students'}
                </button>
              </div>

              <AnimatePresence>
                {expanded === cls.id && (
                  <motion.div initial={{ height: 0, opacity: 0 }} animate={{ height: 'auto', opacity: 1 }} exit={{ height: 0, opacity: 0 }}
                    className="border-t border-gray-50 dark:border-gray-800 overflow-hidden">
                    {!students[cls.id] ? (
                      <div className="p-5 text-center text-gray-400">Loading...</div>
                    ) : students[cls.id].length === 0 ? (
                      <div className="p-5 text-center">
                        <p className="text-gray-400 text-sm">No students have joined yet. Share the code <span className="font-mono font-black text-violet-600">{cls.join_code}</span></p>
                      </div>
                    ) : (
                      <div className="p-4 grid sm:grid-cols-2 lg:grid-cols-3 gap-3">
                        {students[cls.id].map(s => {
                          const acc = s.total_questions > 0 ? Math.round((s.total_correct / s.total_questions) * 100) : 0
                          return (
                            <div key={s.user_id} className="bg-slate-50 dark:bg-gray-800 rounded-xl p-3 flex items-center gap-3">
                              <div className="w-8 h-8 bg-gradient-to-br from-indigo-400 to-violet-500 rounded-full flex items-center justify-center text-white text-xs font-black flex-shrink-0">
                                {s.name[0].toUpperCase()}
                              </div>
                              <div className="min-w-0">
                                <p className="text-sm font-bold text-gray-900 dark:text-white truncate">{s.name}</p>
                                <p className="text-xs text-gray-400">{s.total_correct} correct · {acc}% acc</p>
                              </div>
                            </div>
                          )
                        })}
                      </div>
                    )}
                  </motion.div>
                )}
              </AnimatePresence>
            </motion.div>
          ))}
        </div>
      )}
    </div>
  )
}