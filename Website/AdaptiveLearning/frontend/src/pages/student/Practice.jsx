import { useCallback, useState } from 'react'
import { endPracticeSession } from '../../lib/practiceSession'
import PracticeSetup from '../../components/practice/PracticeSetup'
import PracticeResults from '../../components/practice/PracticeResults'
import PracticeTest from './PracticeTest'
import PracticeFlashcards from './PracticeFlashcards'

/** Router between setup, the mode page, and results; owns only the shared session/result state. */
export default function Practice() {
  const [session, setSession] = useState(null)
  const [result, setResult] = useState(null)
  // Test length: a client-side stopping rule, never sent to the backend. Flashcards ignore it.
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
