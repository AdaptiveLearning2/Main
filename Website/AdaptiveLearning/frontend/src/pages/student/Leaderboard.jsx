import { useEffect, useState } from 'react'
import { motion } from 'framer-motion'
import { Trophy } from 'lucide-react'
import { useAuth } from '../../context/AuthContext'
import { apiFetch } from '../../lib/api'

export default function Leaderboard() {
  const { user } = useAuth()
  const [rows, setRows]       = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError]     = useState(false)

  useEffect(() => {
    apiFetch('/api/leaderboard?limit=20')
      .then(d => { setRows(d); setLoading(false) })
      .catch(() => { setError(true); setLoading(false) })
  }, [])

  const myId = user?.id
  const RANK_BADGE = { 1: 'bg-yellow-400 text-white', 2: 'bg-gray-400 text-white', 3: 'bg-orange-400 text-white' }
  const MEDALS     = ['🥇','🥈','🥉']

  return (
    <div className="p-6 lg:p-8 pb-12">
      <motion.div initial={{ opacity: 0, y: -10 }} animate={{ opacity: 1, y: 0 }} className="mb-6">
        <h1 className="text-3xl font-black text-gray-900 dark:text-white flex items-center gap-3">
          <Trophy className="text-yellow-500" size={28} /> Leaderboard
        </h1>
        <p className="text-gray-500 dark:text-gray-400 mt-1">Top students by correct answers — real data 🔥</p>
      </motion.div>

      {/* podium — top 3 */}
      {!loading && rows.length >= 3 && (
        <div className="grid grid-cols-3 gap-4 mb-8 max-w-sm mx-auto">
          {[rows[1], rows[0], rows[2]].map((p, i) => {
            const sizes   = ['mt-8', 'mt-0', 'mt-12']
            const heights = ['h-16', 'h-24', 'h-12']
            const colors  = ['bg-gray-400', 'bg-yellow-400', 'bg-orange-400']
            return (
              <motion.div key={p.user_id}
                initial={{ opacity: 0, y: 30 }} animate={{ opacity: 1, y: 0 }}
                transition={{ delay: i * 0.1 + 0.1 }}
                className={`flex flex-col items-center ${sizes[i]}`}
              >
                <div className="text-2xl mb-1">{['🥈','🥇','🥉'][i]}</div>
                <div className="w-12 h-12 bg-gradient-to-br from-indigo-400 to-violet-500 rounded-full flex items-center justify-center text-white font-black text-sm mb-1 shadow-md">
                  {(p.display_name || '?')[0].toUpperCase()}
                </div>
                <p className="text-xs font-black text-gray-900 dark:text-white text-center leading-tight">{p.display_name}</p>
                <p className="text-xs text-gray-400 mb-2">{p.total_correct} pts</p>
                <div className={`w-full ${heights[i]} ${colors[i]} rounded-t-xl flex items-center justify-center text-white font-black text-lg shadow`}>
                  {p.rank}
                </div>
              </motion.div>
            )
          })}
        </div>
      )}

      {loading ? (
        <div className="space-y-3">{[1,2,3,4,5].map(i => <div key={i} className="h-16 bg-white dark:bg-gray-900 rounded-2xl border border-gray-100 dark:border-gray-800 animate-pulse" />)}</div>
      ) : error ? (
        <div className="text-center py-12">
          <p className="text-4xl mb-3">⚠️</p>
          <p className="text-gray-500 dark:text-gray-400">Couldn't load leaderboard. Make sure the backend is running.</p>
        </div>
      ) : rows.length === 0 ? (
        <div className="text-center py-12">
          <p className="text-4xl mb-3">🏁</p>
          <p className="text-gray-500 dark:text-gray-400">No students have answered questions yet. Be the first!</p>
        </div>
      ) : (
        <div className="bg-white dark:bg-gray-900 rounded-2xl border border-gray-100 dark:border-gray-800 shadow-sm overflow-hidden">
          {rows.map((p, i) => {
            const acc  = p.total_questions > 0 ? Math.round((p.total_correct / p.total_questions) * 100) : 0
            const isMe = p.user_id === myId
            return (
              <motion.div key={p.user_id}
                initial={{ opacity: 0, x: -10 }} animate={{ opacity: 1, x: 0 }} transition={{ delay: i * 0.03 + 0.2 }}
                whileHover={{ x: 4 }}
                className={`flex items-center justify-between px-5 py-4 border-b border-gray-50 dark:border-gray-800 last:border-0 transition-colors hover:bg-slate-50 dark:hover:bg-gray-800 ${isMe ? 'ring-2 ring-inset ring-indigo-400' : ''}`}
              >
                <div className="flex items-center gap-4">
                  <div className={`w-8 h-8 rounded-full flex items-center justify-center font-black text-sm flex-shrink-0 ${RANK_BADGE[p.rank] || 'bg-gray-100 dark:bg-gray-700 text-gray-500'}`}>
                    {p.rank <= 3 ? MEDALS[p.rank - 1] : p.rank}
                  </div>
                  <div className="w-9 h-9 bg-gradient-to-br from-indigo-400 to-violet-500 rounded-full flex items-center justify-center text-white text-xs font-black flex-shrink-0">
                    {(p.display_name || '?')[0].toUpperCase()}
                  </div>
                  <div>
                    <p className="font-bold text-gray-900 dark:text-white text-sm">
                      {p.display_name}
                      {isMe && <span className="ml-2 text-indigo-600 text-xs font-semibold">(You)</span>}
                    </p>
                    <p className="text-xs text-gray-400">🔥 {p.current_streak} day streak</p>
                  </div>
                </div>
                <div className="flex items-center gap-6 text-right">
                  <div className="hidden sm:block">
                    <p className="text-sm font-black text-gray-900 dark:text-white">{acc}%</p>
                    <p className="text-xs text-gray-400">accuracy</p>
                  </div>
                  <div>
                    <p className="text-lg font-black text-indigo-600">{p.total_correct}</p>
                    <p className="text-xs text-gray-400">correct</p>
                  </div>
                </div>
              </motion.div>
            )
          })}
        </div>
      )}
    </div>
  )
}