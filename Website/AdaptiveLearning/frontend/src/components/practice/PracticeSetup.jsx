import { useCallback, useEffect, useState } from 'react'
import { motion } from 'framer-motion'
import { toast } from 'sonner'
import { apiFetch } from '../../lib/api'
import LoadError from '../ui/LoadError'
import PracticeHistory from './PracticeHistory'
import { TOPIC_ICONS, topicLabel } from '../../lib/topics'
import { useGradeTopicsState } from '../../hooks/useGradeTopics'

const DIFFICULTIES = ['easy', 'medium', 'hard']
// Adaptive's goal rungs minus "No limit": a test has no Finish button, so the count is a real cap.
const QUESTION_COUNTS = [5, 10, 15, 20]
const GRADES = ['Kindergarten', '1st Grade', '2nd Grade', '3rd Grade', '4th Grade', '5th Grade',
  '6th Grade', '7th Grade', '8th Grade', 'Highschool', 'College']
const ICONS = TOPIC_ICONS

/**
 * The practice picker (topics, difficulty, grade, Test vs Flashcard), then
 * `POST /api/practice-sessions/start`.
 *
 * @param onStart  called with `(session, questionCount)`; the count is frontend-only.
 */
export default function PracticeSetup({ onStart }) {
  // `undefined` until the profile is read; the topic rows follow it (`useGradeTopicsState`).
  const [grade, setGrade] = useState(undefined)
  const [topicsAttempt, setTopicsAttempt] = useState(0)
  const { rows: topics, error: topicsError } = useGradeTopicsState(grade, topicsAttempt)
  const [selectedTopics, setSelectedTopics] = useState([])
  const [difficulty, setDifficulty] = useState('medium')
  const [mode, setMode] = useState('test')
  const [questionCount, setQuestionCount] = useState(10)
  const [history, setHistory] = useState([])
  const [loading, setLoading] = useState(true)
  const [failed, setFailed] = useState(false)
  const [starting, setStarting] = useState(false)

  const load = useCallback(async () => {
    setLoading(true)
    setFailed(false)
    try {
      const profile = await apiFetch('/api/profile/me')
      // The backend's `grade_levels.DEFAULT_GRADE`, which the test reads.
      setGrade(profile?.grade_level || '1st Grade')
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

  // What is sent: a pick the grade no longer allows drops out, derived rather than pruned.
  const chosen = selectedTopics.filter(name => (topics || []).some(t => t.name === name && t.allowed))

  function toggleTopic(name, allowed) {
    if (!allowed) return
    setSelectedTopics(sel => sel.includes(name) ? sel.filter(t => t !== name) : [...sel, name])
  }

  async function handleStart() {
    if (!chosen.length || starting) return
    setStarting(true)
    try {
      const session = await apiFetch('/api/practice-sessions/start', {
        method: 'POST',
        body: { mode, topics: chosen, difficulty, grade },
      })
      onStart(session, questionCount)
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
          {topicsError && <LoadError what="topics" error={topicsError}
                                     onRetry={() => setTopicsAttempt(a => a + 1)} />}
          {!topicsError && topics === null && (
            <p className="text-sm text-gray-600 dark:text-gray-400">Loading topics…</p>
          )}
          <div className="flex flex-wrap gap-2">
            {(topics || []).map(t => {
              const isSelected = chosen.includes(t.name)
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
                  {topicLabel(t.name)}
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
          <select id="practice-grade" value={grade} onChange={e => setGrade(e.target.value)}
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

        {/* Test only: flashcards have no deck size. */}
        {mode === 'test' && (
          <div>
            <h3 className="text-sm font-bold text-gray-700 dark:text-gray-200 mb-3">How many questions?</h3>
            <div className="flex gap-2">
              {QUESTION_COUNTS.map(n => (
                <button key={n} type="button" onClick={() => setQuestionCount(n)}
                  aria-label={`${n} questions`} aria-pressed={questionCount === n}
                  className={`flex-1 py-2 rounded-xl text-sm font-bold transition border-2
                    ${questionCount === n ? 'border-indigo-500 bg-indigo-50 dark:bg-indigo-900/30 text-indigo-700 dark:text-indigo-300'
                      : 'border-gray-200 dark:border-gray-700 text-gray-700 dark:text-gray-200 hover:border-indigo-300'}`}
                >
                  {n}
                </button>
              ))}
            </div>
          </div>
        )}

        <button onClick={handleStart} disabled={!chosen.length || starting}
          className="w-full py-3 bg-gradient-to-r from-indigo-600 to-violet-600 text-white rounded-xl font-bold hover:from-indigo-700 hover:to-violet-700 transition shadow disabled:opacity-40 disabled:cursor-not-allowed">
          {starting ? 'Starting…' : chosen.length ? 'Start Practice →' : 'Pick at least one topic'}
        </button>
      </div>

      <PracticeHistory sessions={history} />
    </div>
  )
}
