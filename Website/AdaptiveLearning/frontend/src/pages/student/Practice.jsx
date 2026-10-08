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
    // Closed before Finish (the sweep): the stored counts, since the tally includes refused answers.
    if (closed?.already_closed && typeof closed.questions_answered === 'number') {
      setSession(s => (s ? { ...s, questions_answered: closed.questions_answered,
                             correct_answers: closed.correct_answers ?? 0 } : s))
    }
    setResult({ topic_summary: closed?.topic_summary || {}, alreadyClosed: !!closed?.already_closed })
  }, [session])

  const handleRestart = useCallback(() => {
    setSession(null)
    setResult(null)
  }, [])

  if (!session) return <PracticeSetup onStart={handleStart} />

  if (result) {
    return <PracticeResults session={session} result={result} onRestart={handleRestart} />
  }

  // Keyed on the session, so a new one is a fresh mount: the mode pages' first load relies on it.
  return session.mode === 'flashcard'
    ? <PracticeFlashcards key={session.id} session={session} onFinish={handleFinish} onRestart={handleRestart} />
    : <PracticeTest key={session.id} session={session} onFinish={handleFinish} onRestart={handleRestart}
                    questionCount={questionCount} />
}
