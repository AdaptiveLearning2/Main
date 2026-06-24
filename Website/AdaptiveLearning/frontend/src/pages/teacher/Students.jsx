import { useEffect, useState } from 'react'
import { motion } from 'framer-motion'
import { Users, Search } from 'lucide-react'
import { supabase } from '../../lib/supabase'

export default function Students() {
  const [students, setStudents] = useState([])
  const [loading, setLoading]   = useState(true)
  const [search, setSearch]     = useState('')

  useEffect(() => {
    // pull users who registered with the 'student' role
    supabase
      .from('profiles')
      .select('*')
      .eq('role', 'student')
      .then(({ data, error }) => {
        if (!error) setStudents(data || [])
        setLoading(false)
      })
      .catch(() => setLoading(false))
  }, [])

  const filtered = students.filter(s =>
    (s.email || s.username || s.id || '').toLowerCase().includes(search.toLowerCase())
  )

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
            return (
              <motion.div key={s.id}
                initial={{ opacity: 0, x: -8 }} animate={{ opacity: 1, x: 0 }} transition={{ delay: i * 0.03 }}
                whileHover={{ x: 3 }}
                className="grid grid-cols-4 items-center px-5 py-4 border-b border-gray-50 dark:border-gray-800 last:border-0 hover:bg-slate-50 dark:hover:bg-gray-800 transition-colors"
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
                <div className="flex justify-end">
                  <span className="text-xs font-bold px-2.5 py-1 bg-indigo-100 dark:bg-indigo-900/40 text-indigo-700 dark:text-indigo-300 rounded-full">Student</span>
                </div>
              </motion.div>
            )
          })}
        </div>
      )}
    </div>
  )
}