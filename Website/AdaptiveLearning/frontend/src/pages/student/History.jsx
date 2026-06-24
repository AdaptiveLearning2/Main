import { useEffect, useState } from 'react'
import { motion } from 'framer-motion'
import { apiFetch } from '../../lib/api'

export default function History() {
  const [sessions, setSessions] = useState([])
  const [loading, setLoading]   = useState(true)
  const [filter, setFilter]     = useState('all')

  useEffect(() => {
    apiFetch('/api/sessions')
      .then(s => { setSessions(s || []); setLoading(false) })
      .catch(() => setLoading(false))
  }, [])

  const filtered = sessions.filter(s => {
    if (filter === 'complete')   return !!s.ended_at
    if (filter === 'inprogress') return !s.ended_at
    return true
  })

  const totalQ   = sessions.reduce((a, s) => a + (s.questions_answered || 0), 0)
  const totalC   = sessions.reduce((a, s) => a + (s.correct_answers || 0), 0)
  const overallA = totalQ > 0 ? Math.round((totalC / totalQ) * 100) : 0

  return (
    <div className="p-6 lg:p-8 pb-12">
      <motion.div initial={{ opacity: 0, y: -10 }} animate={{ opacity: 1, y: 0 }} className="mb-6">
        <h1 className="text-3xl font-black text-gray-900 dark:text-white">Session History</h1>
        <p className="text-gray-500 dark:text-gray-400 mt-1">All your past practice sessions.</p>
      </motion.div>

      {sessions.length > 0 && (
        <div className="grid grid-cols-3 gap-4 mb-6">
          {[
            { label: 'Total Sessions', value: sessions.length, icon: '📋' },
            { label: 'Questions Done',  value: totalQ,          icon: '📝' },
            { label: 'Overall Accuracy', value: `${overallA}%`, icon: '🎯' },
          ].map((c, i) => (
            <motion.div key={c.label}
              initial={{ opacity: 0, y: 16 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: i * 0.08 }}
              className="bg-white dark:bg-gray-900 rounded-2xl border border-gray-100 dark:border-gray-800 p-4 shadow-sm text-center"
            >
              <div className="text-2xl mb-1">{c.icon}</div>
              <div className="text-xl font-black text-gray-900 dark:text-white">{c.value}</div>
              <div className="text-xs text-gray-500 dark:text-gray-400">{c.label}</div>
            </motion.div>
          ))}
        </div>
      )}

      <div className="flex gap-2 mb-5">
        {['all','complete','inprogress'].map(f => (
          <button key={f} onClick={() => setFilter(f)}
            className={`px-4 py-1.5 rounded-full text-sm font-semibold transition capitalize ${filter === f ? 'bg-indigo-600 text-white shadow' : 'bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-700 text-gray-600 dark:text-gray-300 hover:border-indigo-300'}`}>
            {f === 'inprogress' ? 'In Progress' : f.charAt(0).toUpperCase() + f.slice(1)}
          </button>
        ))}
      </div>

      {loading ? (
        <div className="space-y-3">{[1,2,3,4].map(i => <div key={i} className="h-20 bg-white dark:bg-gray-900 rounded-2xl border border-gray-100 dark:border-gray-800 animate-pulse" />)}</div>
      ) : filtered.length === 0 ? (
        <div className="text-center py-16">
          <div className="text-6xl mb-4">📭</div>
          <h3 className="text-xl font-black text-gray-900 dark:text-white mb-2">No sessions here</h3>
          <p className="text-gray-500 dark:text-gray-400 text-sm">Start a practice session to see it here.</p>
        </div>
      ) : (
        <div className="space-y-3">
          {filtered.map((s, i) => {
            const acc  = s.questions_answered > 0 ? Math.round((s.correct_answers / s.questions_answered) * 100) : 0
            const done = !!s.ended_at
            return (
              <motion.div key={s.id}
                initial={{ opacity: 0, x: -10 }} animate={{ opacity: 1, x: 0 }} transition={{ delay: i * 0.04 }}
                whileHover={{ x: 4 }}
                className="bg-white dark:bg-gray-900 rounded-2xl border border-gray-100 dark:border-gray-800 p-5 shadow-sm flex items-center justify-between hover:shadow-md transition-shadow"
              >
                <div className="flex items-center gap-4">
                  <div className={`w-11 h-11 rounded-xl flex items-center justify-center text-lg flex-shrink-0 ${done ? 'bg-green-50 dark:bg-green-900/30' : 'bg-amber-50 dark:bg-amber-900/30'}`}>
                    {done ? '✅' : '⏳'}
                  </div>
                  <div>
                    <p className="font-bold text-gray-900 dark:text-white">{s.title || 'Practice Session'}</p>
                    <p className="text-xs text-gray-400 mt-0.5">
                      {new Date(s.started_at).toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric', hour: '2-digit', minute: '2-digit' })}
                    </p>
                  </div>
                </div>
                <div className="flex items-center gap-6 text-right">
                  <div className="hidden sm:block">
                    <p className="text-sm font-black text-gray-900 dark:text-white">{s.questions_answered}</p>
                    <p className="text-xs text-gray-400">questions</p>
                  </div>
                  <div className="hidden sm:block">
                    <p className="text-sm font-black text-gray-900 dark:text-white">{s.correct_answers}</p>
                    <p className="text-xs text-gray-400">correct</p>
                  </div>
                  <div>
                    <p className={`text-lg font-black ${acc >= 70 ? 'text-green-500' : acc >= 40 ? 'text-amber-500' : 'text-rose-500'}`}>{acc}%</p>
                    <p className="text-xs text-gray-400">accuracy</p>
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