import { useCallback, useEffect, useState } from 'react'
import { motion } from 'framer-motion'
import { toast } from 'sonner'
import { apiFetch } from '../../lib/api'
import LoadError from '../ui/LoadError'
import PracticeHistory from './PracticeHistory'

const DIFFICULTIES = ['easy', 'medium', 'hard']
const GRADES = ['1st Grade', '2nd Grade', '3rd Grade', '4th Grade', '5th Grade',
  '6th Grade', '7th Grade', '8th Grade', 'Highschool', 'College']
// Same topic->icon map Adaptive.jsx keeps for its own picker -- read-only
// here, not a shared import, since that page has no test coverage and this
// change deliberately doesn't touch it.
const ICONS = {
  ordering: '🔢', rationals: '➗', expressions: '📐', algebra: '🔣', geometry: '📏',
  angle_relationships: '📐', mean: '〰️', median: '📊', mode: '🔁', probability: '🎲',
}

/** The Quizlet-style picker: topic(s), difficulty, grade, and Test vs
 * Flashcard mode, then `POST /api/practice-sessions/start`.
 *
 * @param onStart  called with the started session row
 */
export default function PracticeSetup({ onStart }) {
  const [topics, setTopics] = useState([])
  const [grade, setGrade] = useState('5th Grade')
  const [selectedTopics, setSelectedTopics] = useState([])
  const [difficulty, setDifficulty] = useState('medium')
  const [mode, setMode] = useState('test')
  const [history, setHistory] = useState([])
  const [loading, setLoading] = useState(true)
  const [failed, setFailed] = useState(false)
  const [starting, setStarting] = useState(false)

  const load = useCallback(async () => {
    setLoading(true)
    setFailed(false)
    try {
      const profile = await apiFetch('/api/profile/me')
      const g = profile?.grade_level || '5th Grade'
      setGrade(g)
      const t = await apiFetch(`/api/topics?grade=${encodeURIComponent(g)}`)
      setTopics(t || [])
      // Best-effort: a failed history read shouldn't block starting a session.
      apiFetch('/api/practice-sessions').then(setHistory).catch(() => {})
    } catch (e) {
      console.error('Failed to load the practice setup screen:', e)
      setFailed(true)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { load() }, [load])

  // Grade is changed from a `<select>`'s onChange -- a user action, so this
  // refetches imperatively from the handler rather than reacting to `grade`
  // in an effect (the two shapes CLAUDE.md calls out: an event handler stays
  // a handler, it doesn't need to become derived state).
  async function handleGradeChange(newGrade) {
    setGrade(newGrade)
    try {
      const t = await apiFetch(`/api/topics?grade=${encodeURIComponent(newGrade)}`)
      setTopics(t || [])
      setSelectedTopics(sel => sel.filter(name => (t || []).some(x => x.name === name && x.allowed)))
    } catch (e) {
      console.error('Failed to refresh topics for the new grade:', e)
    }
  }

  function toggleTopic(name, allowed) {
    if (!allowed) return
    setSelectedTopics(sel => sel.includes(name) ? sel.filter(t => t !== name) : [...sel, name])
  }

  async function handleStart() {
    if (!selectedTopics.length || starting) return
    setStarting(true)
    try {
      const session = await apiFetch('/api/practice-sessions/start', {
        method: 'POST',
        body: { mode, topics: selectedTopics, difficulty, grade },
      })
      onStart(session)
    } catch (e) {
      console.error('Failed to start a practice session:', e)
      toast.error('Could not start that practice session.')
    } finally {
      setStarting(false)
    }
  }

  if (loading) return (
    <div className="min-h-[60vh] flex items-center justify-center">
      <motion.div animate={{ rotate: 360 }} transition={{ duration: 1, repeat: Infinity, ease: 'linear' }}
        className="w-12 h-12 border-4 border-indigo-600 border-t-transparent rounded-full" />
    </div>
  )

  if (failed) return (
    <div className="max-w-lg mx-auto px-4 py-8">
      <LoadError what="practice setup" onRetry={load} />
    </div>
  )

  return (
    <div className="max-w-2xl mx-auto px-4 py-8">
      <h1 className="text-2xl font-black text-gray-900 dark:text-white mb-1">Practice</h1>
      <p className="text-gray-500 dark:text-gray-400 mb-6">
        Pick what to study -- questions are generated for you, on the spot.
      </p>

      <div className="bg-white dark:bg-gray-900 rounded-2xl border border-gray-100 dark:border-gray-800 p-6 space-y-6">
        <div>
          <h3 className="text-sm font-bold text-gray-700 dark:text-gray-200 mb-3">Topics</h3>
          <div className="flex flex-wrap gap-2">
            {topics.map(t => {
              const isSelected = selectedTopics.includes(t.name)
              return (
                <button key={t.name} type="button" onClick={() => toggleTopic(t.name, t.allowed)}
                  disabled={!t.allowed}
                  title={t.allowed ? undefined : 'Not available at this grade'}
                  className={`px-3 py-2 rounded-xl text-sm font-bold capitalize transition border-2 flex items-center gap-1.5
                    ${!t.allowed ? 'opacity-40 cursor-not-allowed border-gray-100 dark:border-gray-800 text-gray-600 dark:text-gray-400'
                      : isSelected ? 'border-indigo-500 bg-indigo-50 dark:bg-indigo-900/30 text-indigo-700 dark:text-indigo-300'
                        : 'border-gray-200 dark:border-gray-700 text-gray-700 dark:text-gray-200 hover:border-indigo-300'}`}
                >
                  <span>{ICONS[t.name] || '📘'}</span>
                  {t.name.replace(/_/g, ' ')}
                </button>
              )
            })}
          </div>
        </div>

        <div>
          <h3 className="text-sm font-bold text-gray-700 dark:text-gray-200 mb-3">Difficulty</h3>
          <div className="flex gap-2">
            {DIFFICULTIES.map(d => (
              <button key={d} type="button" onClick={() => setDifficulty(d)}
                className={`flex-1 py-2 rounded-xl text-sm font-bold capitalize transition border-2
                  ${difficulty === d ? 'border-indigo-500 bg-indigo-50 dark:bg-indigo-900/30 text-indigo-700 dark:text-indigo-300'
                    : 'border-gray-200 dark:border-gray-700 text-gray-700 dark:text-gray-200 hover:border-indigo-300'}`}
              >
                {d}
              </button>
            ))}
          </div>
        </div>

        <div>
          <label htmlFor="practice-grade" className="text-sm font-bold text-gray-700 dark:text-gray-200 mb-3 block">
            Grade
          </label>
          <select id="practice-grade" value={grade} onChange={e => handleGradeChange(e.target.value)}
            className="w-full px-3 py-2 rounded-xl border-2 border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100 text-sm font-bold">
            {GRADES.map(g => <option key={g} value={g}>{g}</option>)}
          </select>
        </div>

        <div>
          <h3 className="text-sm font-bold text-gray-700 dark:text-gray-200 mb-3">Mode</h3>
          <div className="flex gap-2">
            <button type="button" onClick={() => setMode('test')}
              className={`flex-1 py-3 rounded-xl text-sm font-bold transition border-2 text-left px-4
                ${mode === 'test' ? 'border-indigo-500 bg-indigo-50 dark:bg-indigo-900/30 text-indigo-700 dark:text-indigo-300'
                  : 'border-gray-200 dark:border-gray-700 text-gray-700 dark:text-gray-200 hover:border-indigo-300'}`}
            >
              📝 Test
              <div className="font-normal text-xs mt-0.5 opacity-75">Timed, scored questions</div>
            </button>
            <button type="button" onClick={() => setMode('flashcard')}
              className={`flex-1 py-3 rounded-xl text-sm font-bold transition border-2 text-left px-4
                ${mode === 'flashcard' ? 'border-indigo-500 bg-indigo-50 dark:bg-indigo-900/30 text-indigo-700 dark:text-indigo-300'
                  : 'border-gray-200 dark:border-gray-700 text-gray-700 dark:text-gray-200 hover:border-indigo-300'}`}
            >
              🗂️ Flashcards
              <div className="font-normal text-xs mt-0.5 opacity-75">Self-paced, flip to reveal</div>
            </button>
          </div>
        </div>

        <button onClick={handleStart} disabled={!selectedTopics.length || starting}
          className="w-full py-3 bg-gradient-to-r from-indigo-600 to-violet-600 text-white rounded-xl font-bold hover:from-indigo-700 hover:to-violet-700 transition shadow disabled:opacity-40 disabled:cursor-not-allowed">
          {starting ? 'Starting…' : selectedTopics.length ? 'Start Practice →' : 'Pick at least one topic'}
        </button>
      </div>

      <PracticeHistory sessions={history} />
    </div>
  )
}
