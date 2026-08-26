import { useState } from 'react'
import { motion } from 'framer-motion'
import { Link } from 'react-router-dom'
import { apiFetch } from '../../lib/api'
import { useAuth } from '../../context/AuthContext'

/** The wrap-up screen: score (test mode) or cards-reviewed count (flashcard
 * mode), a per-topic breakdown from the closed session's `topic_summary`,
 * and an on-demand AI study-tips panel.
 *
 * Tips are fetched only on request, not on mount -- this reuses
 * `/api/students/{id}/learning-strategies`'s existing bounded LLM pass
 * (rate limit, timeout, waiter cap), and a page that always fired it on
 * every finished session would be the heaviest call on this page happening
 * unconditionally.
 */
export default function PracticeResults({ session, result, onRestart }) {
  const { user } = useAuth()
  const isTest = session.mode === 'test'
  const topicSummary = result?.topic_summary || {}
  const topics = Object.entries(topicSummary)

  const [tips, setTips] = useState(null)
  const [tipsLoading, setTipsLoading] = useState(false)
  const [tipsFailed, setTipsFailed] = useState(false)

  async function loadTips() {
    if (!user?.id) return
    setTipsLoading(true)
    setTipsFailed(false)
    try {
      const res = await apiFetch(`/api/students/${user.id}/learning-strategies`, {
        method: 'POST',
        body: { practice_session_id: session.id },
      })
      setTips(res)
    } catch (e) {
      console.error('Failed to load study tips:', e)
      setTipsFailed(true)
    } finally {
      setTipsLoading(false)
    }
  }

  const finalAcc = isTest && session.questions_answered
    ? Math.round((session.correct_answers / session.questions_answered) * 100)
    : null

  return (
    <div className="max-w-lg mx-auto px-4 py-16 text-center">
      <motion.div initial={{ scale: 0 }} animate={{ scale: 1 }} transition={{ type: 'spring', stiffness: 150 }}>
        <div className="text-7xl mb-4">{isTest ? '🏆' : '📚'}</div>
        <h2 className="text-3xl font-black text-gray-900 dark:text-white mb-1">
          {isTest ? 'Practice Complete!' : 'Review Complete!'}
        </h2>
        {isTest ? (
          <p className="text-gray-500 dark:text-gray-400 mb-6">
            You scored {session.correct_answers} out of {session.questions_answered}
          </p>
        ) : (
          <p className="text-gray-500 dark:text-gray-400 mb-6">
            You reviewed {session.questions_answered} card{session.questions_answered === 1 ? '' : 's'}
          </p>
        )}

        {finalAcc !== null && (
          <div className="bg-white dark:bg-gray-900 rounded-2xl border border-gray-100 dark:border-gray-800 p-6 mb-6 shadow-sm">
            <p className="text-5xl font-black text-indigo-600 mb-1">{finalAcc}%</p>
            <div className="mt-4 bg-gray-100 dark:bg-gray-800 rounded-full h-3 overflow-hidden">
              <motion.div className="h-full bg-gradient-to-r from-indigo-500 to-violet-500 rounded-full"
                initial={{ width: 0 }} animate={{ width: `${finalAcc}%` }} transition={{ duration: 0.8, delay: 0.3 }} />
            </div>
          </div>
        )}

        {topics.length > 0 && (
          <div className="bg-white dark:bg-gray-900 rounded-2xl border border-gray-100 dark:border-gray-800 p-5 mb-6 text-left">
            <h3 className="text-sm font-bold text-gray-500 dark:text-gray-400 mb-3 uppercase tracking-wide">By topic</h3>
            <div className="space-y-2">
              {topics.map(([topic, stats]) => (
                <div key={topic} className="flex items-center justify-between text-sm">
                  <span className="capitalize text-gray-700 dark:text-gray-200">{topic.replace(/_/g, ' ')}</span>
                  <span className="text-gray-500 dark:text-gray-400">
                    {stats.correct !== null && stats.correct !== undefined ? `${stats.correct}% · ` : ''}
                    {stats.attempted} question{stats.attempted === 1 ? '' : 's'}
                  </span>
                </div>
              ))}
            </div>
          </div>
        )}

        <div className="bg-white dark:bg-gray-900 rounded-2xl border border-gray-100 dark:border-gray-800 p-5 mb-8 text-left">
          <h3 className="text-sm font-bold text-gray-500 dark:text-gray-400 mb-3 uppercase tracking-wide">AI study tips</h3>
          {!tips && !tipsLoading && !tipsFailed && (
            <button onClick={loadTips}
              className="w-full py-2.5 bg-indigo-50 dark:bg-indigo-900/30 text-indigo-700 dark:text-indigo-300 rounded-xl font-bold hover:bg-indigo-100 dark:hover:bg-indigo-900/50 transition">
              Get study tips
            </button>
          )}
          {tipsLoading && <p className="text-sm text-gray-500 dark:text-gray-400">Thinking…</p>}
          {tipsFailed && (
            <div>
              <p className="text-sm text-gray-500 dark:text-gray-400 mb-2">Couldn&apos;t get study tips.</p>
              <button onClick={loadTips} className="text-sm font-bold text-indigo-600 dark:text-indigo-400">Try again</button>
            </div>
          )}
          {tips && (
            <div>
              <ul className="space-y-2 text-sm text-gray-700 dark:text-gray-200 list-disc list-inside">
                {(tips.strategies || []).map((s, i) => <li key={i}>{s}</li>)}
              </ul>
              <p className="text-xs text-gray-600 dark:text-gray-400 mt-3">
                {tips.source === 'model-refined' ? 'AI-refined' : 'Rule-based'} suggestions
              </p>
            </div>
          )}
        </div>

        <div className="flex gap-3 justify-center">
          <button onClick={onRestart}
            className="px-6 py-3 bg-indigo-600 text-white rounded-xl font-bold hover:bg-indigo-700 transition">
            Practice Again
          </button>
          <Link to="/dashboard"
            className="px-6 py-3 bg-gray-100 dark:bg-gray-800 text-gray-700 dark:text-gray-200 rounded-xl font-bold hover:bg-gray-200 dark:hover:bg-gray-700 transition">
            Dashboard
          </Link>
        </div>
      </motion.div>
    </div>
  )
}
