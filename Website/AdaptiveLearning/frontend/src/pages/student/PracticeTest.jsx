import { useCallback, useEffect, useRef, useState } from 'react'
import { motion } from 'framer-motion'
import { apiFetch } from '../../lib/api'
import { recordPracticeAnswer } from '../../lib/practiceSession'
import LoadError from '../../components/ui/LoadError'
import QuestionCard from '../../components/practice/QuestionCard'
import { normalizeQuestion, normalizeValue } from '../../lib/practiceQuestion'

const TIMER = 60
const QUESTION_COUNT = 10

/** Test mode: sequential, timed, scored -- adapted from the old Practice.jsx,
 * but pulling one AI-generated question at a time from
 * `GET /api/practice-sessions/{id}/question` instead of a static 10-question
 * read, since generation is now on-demand.
 *
 * @param session   the started practice session
 * @param onFinish  called with `{questions_answered, correct_answers}` once
 *                   QUESTION_COUNT questions have been answered
 */
export default function PracticeTest({ session, onFinish }) {
  const [question, setQuestion] = useState(null)
  const [rawId, setRawId] = useState(null)
  const [index, setIndex] = useState(0)
  const [loading, setLoading] = useState(true)
  const [failed, setFailed] = useState(false)
  const [selected, setSelected] = useState(null)
  const [revealed, setRevealed] = useState(false)
  const [timeLeft, setTimeLeft] = useState(TIMER)
  const timerRef = useRef(null)
  // Score/answered live in a ref as well as state so `handleTimeout` (fired
  // from a `setInterval` closure) always reads the latest count rather than
  // one captured when the timer was set up.
  const tallyRef = useRef({ score: 0, answered: 0 })

  const loadQuestion = useCallback(async () => {
    setLoading(true)
    setFailed(false)
    setSelected(null)
    setRevealed(false)
    try {
      const raw = await apiFetch(`/api/practice-sessions/${session.id}/question`)
      const q = normalizeQuestion(raw)
      if (!q) throw new Error('That question could not be shown')
      setRawId(raw.id)
      setQuestion(q)
    } catch (e) {
      console.error('Failed to load a practice question:', e)
      setFailed(true)
    } finally {
      setLoading(false)
    }
  }, [session.id])

  useEffect(() => { loadQuestion() }, [loadQuestion])

  useEffect(() => {
    if (loading || failed || !question) return
    setTimeLeft(TIMER)
    clearInterval(timerRef.current)
    timerRef.current = setInterval(() => {
      setTimeLeft(t => {
        if (t <= 1) { clearInterval(timerRef.current); handleTimeout(); return 0 }
        return t - 1
      })
    }, 1000)
    return () => clearInterval(timerRef.current)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [index, loading, failed, question])

  async function postAnswer(idx) {
    const selectedVal = idx >= 0 ? question.options[idx] : null
    const isCorrect = selectedVal !== null && normalizeValue(selectedVal) === normalizeValue(question.correctAnswer)
    tallyRef.current = {
      score: tallyRef.current.score + (isCorrect ? 1 : 0),
      answered: tallyRef.current.answered + 1,
    }
    await recordPracticeAnswer({
      sessionId: session.id,
      questionId: rawId,
      selectedIndex: idx,
      correct: isCorrect,
    })
  }

  function handleTimeout() {
    if (revealed) return
    setRevealed(true)
    postAnswer(-1)
  }

  async function handleSelect(idx) {
    if (revealed) return
    clearInterval(timerRef.current)
    setSelected(idx)
    setRevealed(true)
    await postAnswer(idx)
  }

  function handleNext() {
    if (index + 1 >= QUESTION_COUNT) {
      onFinish({ questions_answered: tallyRef.current.answered, correct_answers: tallyRef.current.score })
    } else {
      setIndex(i => i + 1)
      loadQuestion()
    }
  }

  if (loading) return (
    <div className="min-h-[60vh] flex items-center justify-center">
      <motion.div animate={{ rotate: 360 }} transition={{ duration: 1, repeat: Infinity, ease: 'linear' }}
        className="w-12 h-12 border-4 border-indigo-600 border-t-transparent rounded-full" />
    </div>
  )

  if (failed || !question) return (
    <div className="max-w-lg mx-auto px-4 py-8">
      <LoadError what="the next question" onRetry={loadQuestion} />
    </div>
  )

  const timerPct = (timeLeft / TIMER) * 100

  return (
    <div className="max-w-2xl mx-auto px-4 py-8">
      <div className="mb-6">
        <div className="flex items-center justify-between text-sm mb-2">
          <span className="text-gray-500 dark:text-gray-400">Question {index + 1} of {QUESTION_COUNT}</span>
          <span className={`font-bold tabular-nums ${timeLeft <= 10 ? 'text-rose-500 animate-pulse' : 'text-gray-700 dark:text-gray-300'}`}>
            ⏱ {timeLeft}s
          </span>
        </div>
        <div className="h-2 bg-gray-100 dark:bg-gray-800 rounded-full overflow-hidden mb-1">
          <div className="h-full bg-gradient-to-r from-indigo-500 to-violet-500 rounded-full transition-all duration-300"
            style={{ width: `${(index / QUESTION_COUNT) * 100}%` }} />
        </div>
        <div className="h-1 bg-gray-100 dark:bg-gray-800 rounded-full overflow-hidden">
          <div className={`h-full rounded-full transition-all duration-1000 ${timeLeft > 20 ? 'bg-green-500' : timeLeft > 10 ? 'bg-amber-500' : 'bg-rose-500'}`}
            style={{ width: `${timerPct}%` }} />
        </div>
      </div>

      <QuestionCard question={question} selected={selected} revealed={revealed} onSelect={handleSelect} />

      {revealed && (
        <motion.div initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} className="mt-6 flex justify-end">
          <button onClick={handleNext}
            className="px-6 py-2.5 bg-gradient-to-r from-indigo-600 to-violet-600 text-white rounded-xl font-bold hover:from-indigo-700 hover:to-violet-700 transition shadow">
            {index + 1 >= QUESTION_COUNT ? 'See Results →' : 'Next →'}
          </button>
        </motion.div>
      )}
    </div>
  )
}
