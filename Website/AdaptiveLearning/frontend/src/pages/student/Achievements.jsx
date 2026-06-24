import { useEffect, useState } from 'react'
import { motion } from 'framer-motion'
import { Star } from 'lucide-react'
import { apiFetch } from '../../lib/api'

const ALL = [
  { id: 'first_q',  emoji: '🎯', title: 'First Steps',      desc: 'Answer your first question',    threshold: 1,   stat: 'total_questions' },
  { id: 'q10',      emoji: '📝', title: 'Getting Started',  desc: 'Answer 10 questions',           threshold: 10,  stat: 'total_questions' },
  { id: 'q50',      emoji: '📚', title: 'Bookworm',         desc: 'Answer 50 questions',           threshold: 50,  stat: 'total_questions' },
  { id: 'q100',     emoji: '💯', title: 'Centurion',        desc: 'Answer 100 questions',          threshold: 100, stat: 'total_questions' },
  { id: 'q500',     emoji: '🏔️', title: 'Mountain Climber', desc: 'Answer 500 questions',          threshold: 500, stat: 'total_questions' },
  { id: 'c1',       emoji: '✅', title: 'Nailed It',        desc: 'Get your first correct answer', threshold: 1,   stat: 'total_correct' },
  { id: 'c25',      emoji: '🌟', title: 'Rising Star',      desc: '25 correct answers',            threshold: 25,  stat: 'total_correct' },
  { id: 'c100',     emoji: '🎖️', title: 'Sharp Mind',       desc: '100 correct answers',           threshold: 100, stat: 'total_correct' },
  { id: 'c250',     emoji: '🧮', title: 'Math Machine',     desc: '250 correct answers',           threshold: 250, stat: 'total_correct' },
  { id: 's3',       emoji: '🔥', title: 'On Fire',          desc: '3-day streak',                  threshold: 3,   stat: 'current_streak' },
  { id: 's7',       emoji: '⚡', title: 'Week Warrior',     desc: '7-day streak',                  threshold: 7,   stat: 'current_streak' },
  { id: 's30',      emoji: '🌙', title: 'Night Owl',        desc: '30-day streak',                 threshold: 30,  stat: 'current_streak' },
  { id: 'b10',      emoji: '🏆', title: 'Top Dog',          desc: 'Best streak of 10',             threshold: 10,  stat: 'best_streak' },
]

export default function Achievements() {
  const [stats, setStats]     = useState(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    apiFetch('/api/stats/me')
      .then(s => { setStats(s); setLoading(false) })
      .catch(() => { setStats({ total_questions: 0, total_correct: 0, current_streak: 0, best_streak: 0 }); setLoading(false) })
  }, [])

  const unlocked = ALL.filter(a => (stats?.[a.stat] ?? 0) >= a.threshold)
  const locked   = ALL.filter(a => (stats?.[a.stat] ?? 0) < a.threshold)
  const pct      = ALL.length > 0 ? Math.round((unlocked.length / ALL.length) * 100) : 0

  return (
    <div className="p-6 lg:p-8 pb-12">
      <motion.div initial={{ opacity: 0, y: -10 }} animate={{ opacity: 1, y: 0 }} className="mb-6">
        <h1 className="text-3xl font-black text-gray-900 dark:text-white flex items-center gap-3">
          <Star className="text-yellow-500" size={28} /> Achievements
        </h1>
        <p className="text-gray-500 dark:text-gray-400 mt-1">
          {loading ? '...' : `${unlocked.length} / ${ALL.length} unlocked`}
        </p>
      </motion.div>

      {!loading && (
        <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ delay: 0.1 }}
          className="bg-white dark:bg-gray-900 rounded-2xl border border-gray-100 dark:border-gray-800 p-5 shadow-sm mb-8">
          <div className="flex justify-between items-center mb-2">
            <span className="text-sm font-bold text-gray-700 dark:text-gray-300">Progress</span>
            <span className="text-sm font-black text-indigo-600">{unlocked.length} / {ALL.length}</span>
          </div>
          <div className="h-3 bg-gray-100 dark:bg-gray-700 rounded-full overflow-hidden">
            <motion.div className="h-full bg-gradient-to-r from-indigo-500 to-violet-500 rounded-full"
              initial={{ width: 0 }} animate={{ width: `${pct}%` }} transition={{ duration: 0.9, delay: 0.2 }} />
          </div>
          <p className="text-xs text-gray-400 mt-1.5">{pct}% complete</p>
        </motion.div>
      )}

      {unlocked.length > 0 && (
        <div className="mb-8">
          <h2 className="text-lg font-black text-gray-900 dark:text-white mb-4">🔓 Unlocked <span className="text-sm font-semibold text-gray-400">({unlocked.length})</span></h2>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4">
            {unlocked.map((a, i) => (
              <motion.div key={a.id}
                initial={{ opacity: 0, scale: 0.85 }} animate={{ opacity: 1, scale: 1 }}
                transition={{ delay: i * 0.05, type: 'spring', stiffness: 150 }}
                whileHover={{ y: -4 }}
                className="bg-white dark:bg-gray-900 rounded-2xl border-2 border-indigo-200 dark:border-indigo-800 p-5 shadow-sm hover:shadow-lg transition-shadow relative overflow-hidden">
                <div className="absolute top-0 right-0 w-16 h-16 bg-indigo-50 dark:bg-indigo-900/20 rounded-full -translate-y-6 translate-x-6 pointer-events-none" />
                <div className="text-4xl mb-3">{a.emoji}</div>
                <h3 className="font-black text-gray-900 dark:text-white text-sm mb-1">{a.title}</h3>
                <p className="text-xs text-gray-500 dark:text-gray-400">{a.desc}</p>
                <p className="mt-3 text-xs font-bold text-indigo-600 dark:text-indigo-400">✓ Unlocked</p>
              </motion.div>
            ))}
          </div>
        </div>
      )}

      {locked.length > 0 && (
        <div>
          <h2 className="text-lg font-black text-gray-900 dark:text-white mb-4">🔒 Locked <span className="text-sm font-semibold text-gray-400">({locked.length})</span></h2>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4">
            {locked.map((a, i) => {
              const progress = stats?.[a.stat] ?? 0
              const fill     = Math.min(100, Math.round((progress / a.threshold) * 100))
              return (
                <motion.div key={a.id} initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ delay: i * 0.03 + 0.2 }}
                  className="bg-white dark:bg-gray-900 rounded-2xl border border-gray-100 dark:border-gray-800 p-5 opacity-65 hover:opacity-90 transition-opacity">
                  <div className="text-4xl mb-3 grayscale">{a.emoji}</div>
                  <h3 className="font-black text-gray-700 dark:text-gray-300 text-sm mb-1">{a.title}</h3>
                  <p className="text-xs text-gray-400">{a.desc}</p>
                  <div className="mt-3">
                    <div className="flex justify-between text-xs text-gray-400 mb-1"><span>{progress} / {a.threshold}</span><span>{fill}%</span></div>
                    <div className="h-1.5 bg-gray-100 dark:bg-gray-700 rounded-full overflow-hidden">
                      <div className="h-full bg-gray-300 dark:bg-gray-600 rounded-full" style={{ width: `${fill}%` }} />
                    </div>
                  </div>
                </motion.div>
              )
            })}
          </div>
        </div>
      )}
    </div>
  )
}