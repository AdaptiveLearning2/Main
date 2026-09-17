import { useCallback, useState } from 'react'
import { endPracticeSession } from '../../lib/practiceSession'
import PracticeSetup from '../../components/practice/PracticeSetup'
import PracticeResults from '../../components/practice/PracticeResults'
import PracticeTest from './PracticeTest'
import PracticeFlashcards from './PracticeFlashcards'

/** A thin router between the three states a practice visit moves through:
 * pick topic(s)/difficulty/grade/mode, work through generated questions, see
 * results. The generation, answer-recording, and question-rendering logic
 * live in the mode-specific pages/components this switches between -- this
 * file only owns which one is on screen and the session/result state they
 * share.
 */
export default function Practice() {
  const [session, setSession] = useState(null)
  const [result, setResult] = useState(null)
  // How many questions a Test runs for, chosen on the setup screen. Held here
  // rather than sent to the backend: generation is one question per request,
  // so the count is only ever a client-side stopping rule -- the same shape as
  // Adaptive's question goal. Flashcards ignore it; they have no deck size.
  const [questionCount, setQuestionCount] = useState(10)

  const handleStart = useCallback((s, count = 10) => {
    setSession(s)
    setQuestionCount(count)
    setResult(null)
  }, [])

  const handleFinish = useCallback(async (liveCounts) => {
    setSession(s => (s ? { ...s, ...liveCounts } : s))
    const closed = await endPracticeSession(session?.id)
    setResult({ topic_summary: closed?.topic_summary || {} })
  }, [session])

  const handleRestart = useCallback(() => {
    setSession(null)
    setResult(null)
  }, [])

  if (!session) return <PracticeSetup onStart={handleStart} />

  if (result) {
    return <PracticeResults session={session} result={result} onRestart={handleRestart} />
  }

  return session.mode === 'flashcard'
    ? <PracticeFlashcards session={session} onFinish={handleFinish} />
    : <PracticeTest session={session} onFinish={handleFinish} questionCount={questionCount} />
}
