import { useState, useEffect } from 'react'
import { motion } from 'framer-motion'
import { Users, Hash, GraduationCap } from 'lucide-react'
import { apiFetch } from '../../lib/api'
import { toast } from 'sonner'

export default function JoinClass() {
  const [code, setCode]         = useState('')
  const [loading, setLoading]   = useState(false)
  const [classes, setClasses]   = useState([])
  const [loadingClasses, setLoadingClasses] = useState(true)

  useEffect(() => {
    apiFetch('/api/classes')
      .then(c => { setClasses(c); setLoadingClasses(false) })
      .catch(() => setLoadingClasses(false))
  }, [])

  const handleJoin = async (e) => {
    e.preventDefault()
    if (!code.trim()) return
    setLoading(true)
    try {
      const cls = await apiFetch('/api/classes/join', { method: 'POST', body: { join_code: code.trim().toUpperCase() } })
      toast.success(`Joined "${cls.name}"! 🎉`)
      setCode('')
      setClasses(await apiFetch('/api/classes'))
    } catch (err) {
      toast.error(err.message || 'Could not join class')
    } finally { setLoading(false) }
  }

  return (
    <div className="p-6 lg:p-8 pb-12 max-w-2xl">
      <motion.div initial={{ opacity: 0, y: -10 }} animate={{ opacity: 1, y: 0 }} className="mb-6">
        <h1 className="text-3xl font-black text-gray-900 dark:text-white flex items-center gap-3">
          <Users className="text-indigo-600" size={28} /> Join a Class
        </h1>
        <p className="text-gray-500 dark:text-gray-400 mt-1">Enter the 6-character code your teacher gave you.</p>
      </motion.div>

      <motion.div initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.1 }}
        className="bg-white dark:bg-gray-900 rounded-2xl border border-gray-100 dark:border-gray-800 p-6 shadow-sm mb-8">
        <form onSubmit={handleJoin} className="flex gap-3">
          <div className="relative flex-1">
            <Hash size={16} className="absolute left-3.5 top-1/2 -translate-y-1/2 text-gray-400" />
            <input value={code} onChange={e => setCode(e.target.value.toUpperCase().slice(0, 6))}
              className="w-full pl-10 pr-4 py-3 bg-gray-50 dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded-xl focus:ring-2 focus:ring-indigo-500 dark:text-white outline-none transition tracking-widest font-mono uppercase"
              placeholder="ABC123" maxLength={6} />
          </div>
          <motion.button type="submit" disabled={loading || code.length !== 6}
            whileHover={{ scale: 1.02 }} whileTap={{ scale: 0.97 }}
            className="px-6 py-3 bg-gradient-to-r from-indigo-600 to-violet-600 text-white rounded-xl font-bold shadow disabled:opacity-50 disabled:cursor-not-allowed transition whitespace-nowrap">
            {loading ? '...' : 'Join Class'}
          </motion.button>
        </form>
      </motion.div>

      <h2 className="text-lg font-black text-gray-900 dark:text-white mb-4">My Classes</h2>
      {loadingClasses ? (
        <div className="space-y-2">{[1,2].map(i => <div key={i} className="h-16 bg-white dark:bg-gray-900 rounded-2xl border border-gray-100 dark:border-gray-800 animate-pulse" />)}</div>
      ) : classes.length === 0 ? (
        <div className="text-center py-10 bg-white dark:bg-gray-900 rounded-2xl border border-gray-100 dark:border-gray-800">
          <p className="text-4xl mb-3">🏫</p>
          <p className="text-gray-500 dark:text-gray-400 text-sm">You haven't joined any classes yet.</p>
        </div>
      ) : (
        <div className="space-y-3">
          {classes.map((c, i) => (
            <motion.div key={c.id}
              initial={{ opacity: 0, x: -8 }} animate={{ opacity: 1, x: 0 }} transition={{ delay: i * 0.05 }}
              className="bg-white dark:bg-gray-900 rounded-2xl border border-gray-100 dark:border-gray-800 p-4 shadow-sm flex items-center justify-between">
              <div className="flex items-center gap-3">
                <div className="w-10 h-10 bg-gradient-to-br from-indigo-400 to-violet-500 rounded-xl flex items-center justify-center text-white font-black text-sm shadow">
                  {c.name[0].toUpperCase()}
                </div>
                <div>
                  <p className="font-bold text-gray-900 dark:text-white">{c.name}</p>
                  <p className="text-xs text-gray-400 flex items-center gap-2">
                    Code: <span className="font-mono font-bold">{c.join_code}</span>
                    {c.grade_level && (
                      <span className="inline-flex items-center gap-1 text-indigo-600 dark:text-indigo-400 font-bold">
                        · <GraduationCap size={11} /> {c.grade_level}
                      </span>
                    )}
                  </p>
                </div>
              </div>
              <span className="text-xs font-bold text-green-600 bg-green-100 dark:bg-green-900/30 dark:text-green-400 px-3 py-1 rounded-full">Enrolled ✓</span>
            </motion.div>
          ))}
        </div>
      )}
    </div>
  )
}