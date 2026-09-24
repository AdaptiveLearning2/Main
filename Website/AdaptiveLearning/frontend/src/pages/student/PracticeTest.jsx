import { useCallback, useEffect, useRef, useState } from 'react'
import { motion } from 'framer-motion'
import { apiFetch } from '../../lib/api'
import { recordPracticeAnswer } from '../../lib/practiceSession'
import LoadError from '../../components/ui/LoadError'
import QuestionCard from '../../components/practice/QuestionCard'
import { normalizeQuestion, normalizeValue } from '../../lib/practiceQuestion'

const TIMER = 60

/** Test mode: sequential, timed, scored; one generated question per request.
 * `onFinish` gets `{questions_answered, correct_answers}` after `questionCount` answers.
 */
export default function PracticeTest({ session, onFinish, questionCount = 10 }) {
  const [question, setQuestion] = useState(null)
  const [rawId, setRawId] = useState(null)
  const [index, setIndex] = useState(0)
  const [loading, setLoading] = useState(true)
  const [failed, setFailed] = useState(false)
  const [selected, setSelected] = useState(null)
  const [revealed, setRevealed] = useState(false)
  const [timeLeft, setTimeLeft] = useState(TIMER)
  const timerRef = useRef(null)
  // A ref too, so `postAnswer` from the interval closure reads the latest count.
  const tallyRef = useRef({ score: 0, answered: 0 })
  // Whether this question is answered (click or timeout). A ref, not `revealed`:
  // React may invoke an updater more than once, which double-posted timeouts.
  const answeredRef = useRef(false)
  // In-flight answer promise; `handleNext` awaits it so `/end` can't race `/answer`.
  const pendingAnswerRef = useRef(null)
  // Synchronous re-entrancy guard for `handleNext` (a double click skipped a
  // question); `advancing` state just disables the button.
  const advancingRef = useRef(false)
  const [advancing, setAdvancing] = useState(false)

  // Only the latest load may write state, or a stale question replaces the shown one.
  const requestRef = useRef(0)

  const loadQuestion = useCallback(async () => {
    const mine = ++requestRef.current
    setLoading(true)
    setFailed(false)
    setSelected(null)
    setRevealed(false)
    answeredRef.current = false
    advancingRef.current = false
    setAdvancing(false)
    try {
      const raw = await apiFetch(`/api/practice-sessions/${session.id}/question`)
      if (mine !== requestRef.current) return
      const q = normalizeQuestion(raw)
      if (!q) throw new Error('That question could not be shown')
      setRawId(raw.id)
      setQuestion(q)
    } catch (e) {
      if (mine !== requestRef.current) return
      console.error('Failed to load a practice question:', e)
      setFailed(true)
    } finally {
      if (mine === requestRef.current) setLoading(false)
    }
  }, [session.id])

  // Once per session id, not per effect run: StrictMode double-mounts, and
  // each `/question` is two billed model calls.
  const autoLoadedFor = useRef(null)
  useEffect(() => {
    if (autoLoadedFor.current === session.id) return
    autoLoadedFor.current = session.id
    loadQuestion()
  }, [loadQuestion, session.id])

  useEffect(() => {
    if (loading || failed || !question) return
    setTimeLeft(TIMER)
    clearInterval(timerRef.current)
    // Only decrements; the effect below fires the timeout once, via `answeredRef`.
    timerRef.current = setInterval(() => {
      setTimeLeft(t => (t <= 1 ? 0 : t - 1))
    }, 1000)
    return () => clearInterval(timerRef.current)
  }, [index, loading, failed, question])

  useEffect(() => {
    if (timeLeft > 0 || answeredRef.current) return
    answeredRef.current = true
    clearInterval(timerRef.current)
    setRevealed(true)
    postAnswer(-1)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [timeLeft])

  async function postAnswer(idx) {
    const selectedVal = idx >= 0 ? question.options[idx] : null
    const isCorrect = selectedVal !== null && normalizeValue(selectedVal) === normalizeValue(question.correctAnswer)
    tallyRef.current = {
      score: tallyRef.current.score + (isCorrect ? 1 : 0),
      answered: tallyRef.current.answered + 1,
    }
    // Set before the `await`, since the timeout effect doesn't await `postAnswer`.
    const promise = recordPracticeAnswer({
      sessionId: session.id,
      questionId: rawId,
      selectedIndex: idx,
      correct: isCorrect,
    })
    pendingAnswerRef.current = promise
    try {
      await promise
    } finally {
      if (pendingAnswerRef.current === promise) pendingAnswerRef.current = null
    }
  }

  async function handleSelect(idx) {
    if (answeredRef.current) return
    answeredRef.current = true
    clearInterval(timerRef.current)
    setSelected(idx)
    setRevealed(true)
    await postAnswer(idx)
  }

  async function handleNext() {
    if (advancingRef.current) return
    advancingRef.current = true
    setAdvancing(true)
    // Wait for an in-flight answer so `/end` can't race it.
    if (pendingAnswerRef.current) await pendingAnswerRef.current
    if (index + 1 >= questionCount) {
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
          <span className="text-gray-500 dark:text-gray-400">Question {index + 1} of {questionCount}</span>
          <span className={`font-bold tabular-nums ${timeLeft <= 10 ? 'text-rose-500 animate-pulse' : 'text-gray-700 dark:text-gray-300'}`}>
            ⏱ {timeLeft}s
          </span>
        </div>
        <div className="h-2 bg-gray-100 dark:bg-gray-800 rounded-full overflow-hidden mb-1">
          <div className="h-full bg-gradient-to-r from-indigo-500 to-violet-500 rounded-full transition-all duration-300"
            style={{ width: `${(index / questionCount) * 100}%` }} />
        </div>
        <div className="h-1 bg-gray-100 dark:bg-gray-800 rounded-full overflow-hidden">
          <div className={`h-full rounded-full transition-all duration-1000 ${timeLeft > 20 ? 'bg-green-500' : timeLeft > 10 ? 'bg-amber-500' : 'bg-rose-500'}`}
            style={{ width: `${timerPct}%` }} />
        </div>
      </div>

      <QuestionCard question={question} selected={selected} revealed={revealed} onSelect={handleSelect} />

      {revealed && (
        <motion.div initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} className="mt-6 flex justify-end">
          <button onClick={handleNext} disabled={advancing}
            className="px-6 py-2.5 bg-gradient-to-r from-indigo-600 to-violet-600 text-white rounded-xl font-bold hover:from-indigo-700 hover:to-violet-700 transition shadow disabled:opacity-60 disabled:cursor-not-allowed">
            {index + 1 >= questionCount ? 'See Results →' : 'Next →'}
          </button>
        </motion.div>
      )}
    </div>
  )
}
