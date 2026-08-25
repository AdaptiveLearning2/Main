import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { motion, AnimatePresence } from 'framer-motion'
import { Users, Plus, X, Copy, Check, GraduationCap, Pencil, Save, ChevronRight } from 'lucide-react'
import { apiFetch } from '../../lib/api'
import { toast } from 'sonner'
import SkeletonList from '../../components/ui/Skeleton'
import LoadError from '../../components/ui/LoadError'

const GRADES = ['1st Grade','2nd Grade','3rd Grade','4th Grade','5th Grade','6th Grade','7th Grade','8th Grade','Highschool','College']

export default function Classes() {
  const navigate = useNavigate()
  const [classes, setClasses]     = useState([])
  const [loading, setLoading]     = useState(true)
  const [creating, setCreating]   = useState(false)
  const [newName, setNewName]     = useState('')
  const [newGrade, setNewGrade]   = useState('5th Grade')
  const [showForm, setShowForm]   = useState(false)
  const [failed, setFailed]       = useState(false)
  const [copiedId, setCopiedId]   = useState(null)
  const [editingId, setEditingId] = useState(null)
  const [editGrade, setEditGrade] = useState('')

  useEffect(() => { loadClasses() }, [])

  async function loadClasses() {
    try {
      setClasses(await apiFetch('/api/classes'))
      setFailed(false)
    } catch (e) {
      // Must set failed, not just leave the list empty, or a failed read
      // shows "No classes yet" and invites the teacher to recreate them.
      console.error('Failed to load classes:', e)
      setFailed(true)
    }
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

  function copyCode(code, id, e) {
    e.stopPropagation()
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
        <SkeletonList count={3} height="h-20" />
      ) : failed ? (
        <LoadError what="your classes" onRetry={loadClasses} />
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
              onClick={() => navigate(`/teacher/classes/${cls.id}`)}
              className="bg-white dark:bg-gray-900 rounded-2xl border border-gray-100 dark:border-gray-800 shadow-sm overflow-hidden cursor-pointer hover:border-violet-300 dark:hover:border-violet-700 hover:shadow-md transition">
              <div className="flex items-center justify-between p-5 flex-wrap gap-4">
                <div className="flex items-center gap-4">
                  <div className="w-12 h-12 bg-gradient-to-br from-violet-400 to-purple-500 rounded-xl flex items-center justify-center text-white font-black text-lg shadow">
                    {/* Falls back to '?': an empty name would make `''[0]` undefined and crash on .toUpperCase(). */}
                    {(cls.name || '?')[0].toUpperCase()}
                  </div>
                  <div>
                    <h3 className="font-black text-gray-900 dark:text-white">{cls.name || 'Untitled class'}</h3>
                    <div className="flex items-center gap-2 mt-1 flex-wrap">
                      <span className="text-xs text-gray-500 dark:text-gray-400">Code:</span>
                      <span className="font-mono font-black text-violet-600 dark:text-violet-400 text-sm tracking-widest">{cls.join_code}</span>
                      <button onClick={(e) => copyCode(cls.join_code, cls.id, e)}
                        className="p-1 rounded-md hover:bg-violet-50 dark:hover:bg-violet-900/30 transition">
                        {copiedId === cls.id ? <Check size={13} className="text-green-500" /> : <Copy size={13} className="text-gray-600 dark:text-gray-400" />}
                      </button>

                      {editingId === cls.id ? (
                        <span className="flex items-center gap-1 ml-2" onClick={e => e.stopPropagation()}>
                          <select value={editGrade} onChange={e => setEditGrade(e.target.value)}
                            className="px-2 py-1 text-xs rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 dark:text-white">
                            {GRADES.map(g => <option key={g} value={g}>{g}</option>)}
                          </select>
                          <button onClick={() => saveGrade(cls.id)} className="p-1 rounded-md text-emerald-500 hover:bg-emerald-50 dark:hover:bg-emerald-900/30">
                            <Save size={13} />
                          </button>
                          <button onClick={() => setEditingId(null)} className="p-1 rounded-md text-gray-600 hover:bg-gray-100 dark:hover:bg-gray-800 dark:text-gray-400">
                            <X size={13} />
                          </button>
                        </span>
                      ) : (
                        <span className="flex items-center gap-1 ml-2 text-xs font-bold px-2.5 py-1 bg-indigo-100 dark:bg-indigo-900/40 text-indigo-700 dark:text-indigo-300 rounded-full">
                          <GraduationCap size={11} /> {cls.grade_level || 'Grade not set'}
                          <button onClick={(e) => { e.stopPropagation(); setEditingId(cls.id); setEditGrade(cls.grade_level || '5th Grade') }}
                            className="ml-1 opacity-60 hover:opacity-100"><Pencil size={11} /></button>
                        </span>
                      )}
                    </div>
                  </div>
                </div>
                <div className="flex items-center gap-2 px-4 py-2 bg-violet-50 dark:bg-violet-900/30 text-violet-700 dark:text-violet-300 rounded-xl text-sm font-bold">
                  View Students <ChevronRight size={15} />
                </div>
              </div>
            </motion.div>
          ))}
        </div>
      )}
    </div>
  )
}