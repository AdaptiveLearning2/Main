import { useCallback, useEffect, useRef, useState } from 'react'
import { motion } from 'framer-motion'
import { apiFetch } from '../../lib/api'
import { markPracticeViewed } from '../../lib/practiceSession'
import LoadError from '../../components/ui/LoadError'
import { normalizeValue } from '../../lib/practiceQuestion'

/** Flashcard mode: self-paced, card-flip, no timer and no score. "Done" ends
 * the session at any point -- there is no fixed deck size the way a
 * QUESTION_COUNT test has one.
 *
 * @param session   the started practice session
 * @param onFinish  called with `{questions_answered, correct_answers: 0}`
 *                   when the student ends the deck
 */
export default function PracticeFlashcards({ session, onFinish }) {
  const [question, setQuestion] = useState(null)
  const [loading, setLoading] = useState(true)
  const [failed, setFailed] = useState(false)
  const [flipped, setFlipped] = useState(false)
  const [reviewed, setReviewed] = useState(0)
  const reviewedRef = useRef(0)
  const flippedRef = useRef(false)

  const loadCard = useCallback(async () => {
    setLoading(true)
    setFailed(false)
    setFlipped(false)
    flippedRef.current = false
    try {
      const q = await apiFetch(`/api/practice-sessions/${session.id}/question`)
      setQuestion(q)
    } catch (e) {
      console.error('Failed to load the next card:', e)
      setFailed(true)
    } finally {
      setLoading(false)
    }
  }, [session.id])

  useEffect(() => { loadCard() }, [loadCard])

  async function handleFlip() {
    if (flippedRef.current || !question) return
    flippedRef.current = true
    setFlipped(true)
    reviewedRef.current += 1
    setReviewed(reviewedRef.current)
    await markPracticeViewed({ sessionId: session.id, questionId: question.id })
  }

  function handleNext() {
    loadCard()
  }

  function handleDone() {
    onFinish({ questions_answered: reviewedRef.current, correct_answers: 0 })
  }

  useEffect(() => {
    function onKey(e) {
      if (loading || failed) return
      if (e.code === 'Space') { e.preventDefault(); handleFlip() }
      else if (e.code === 'ArrowRight' && flippedRef.current) handleNext()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [loading, failed, question])

  if (loading) return (
    <div className="min-h-[60vh] flex items-center justify-center">
      <motion.div animate={{ rotate: 360 }} transition={{ duration: 1, repeat: Infinity, ease: 'linear' }}
        className="w-12 h-12 border-4 border-indigo-600 border-t-transparent rounded-full" />
    </div>
  )

  if (failed || !question) return (
    <div className="max-w-lg mx-auto px-4 py-8">
      <LoadError what="the next card" onRetry={loadCard} />
    </div>
  )

  return (
    <div className="max-w-2xl mx-auto px-4 py-8">
      <div className="flex items-center justify-between mb-6 text-sm">
        <span className="text-gray-500 dark:text-gray-400">{reviewed} reviewed</span>
        <button onClick={handleDone} className="font-bold text-indigo-600 dark:text-indigo-400">Done</button>
      </div>

      <button onClick={handleFlip} className="w-full text-left" aria-label="Flip card to reveal the answer">
        <motion.div
          className="bg-white dark:bg-gray-900 rounded-2xl shadow-sm border border-gray-100 dark:border-gray-800 p-10 min-h-[260px] flex flex-col items-center justify-center text-center cursor-pointer"
        >
          {question.question_topic && (
            <span className="text-xs font-bold px-2.5 py-1 bg-indigo-100 dark:bg-indigo-900/40 text-indigo-700 dark:text-indigo-300 rounded-full capitalize mb-4">
              {String(question.question_topic).replace(/_/g, ' ')}
            </span>
          )}
          <p className="text-xl font-semibold text-gray-900 dark:text-white mb-4">{question.question_text}</p>
          {flipped ? (
            <p className="text-2xl font-black text-indigo-600 dark:text-indigo-400">
              {normalizeValue(question.correct_answer)}
            </p>
          ) : (
            <p className="text-sm text-gray-600 dark:text-gray-400">Tap or press Space to reveal the answer</p>
          )}
        </motion.div>
      </button>

      {flipped && (
        <motion.div initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} className="mt-6 flex justify-end">
          <button onClick={handleNext}
            className="px-6 py-2.5 bg-gradient-to-r from-indigo-600 to-violet-600 text-white rounded-xl font-bold hover:from-indigo-700 hover:to-violet-700 transition shadow">
            Next →
          </button>
        </motion.div>
      )}
    </div>
  )
}
