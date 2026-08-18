import { useCallback, useEffect, useState, useRef } from 'react'
import { Link } from 'react-router-dom'
import { motion } from 'framer-motion'
import { apiFetch } from '../../lib/api'
import { endSession, recordAnswer } from '../../lib/session'
import { supabase } from '../../lib/supabase'
import LoadError from '../../components/ui/LoadError'

const TIMER = 60

/** Whether a question can actually be put in front of a student.
 *
 * Two ways a row fails that, and both are silent in their own way.
 *
 * **No options.** The render maps `q.options` and indexes it in three more
 * places, none of them guarded, so such a row threw during render -- and with
 * no error boundary above it that took the whole application down to a blank
 * document rather than skipping a question.
 *
 * **No correct answer.** `questions.correct_answer` is nullable
 * (`20260625000000_init.sql:198`) and correctness is decided by
 * `normalize(picked) === normalize(q.correct_answer)`. `normalize` stringifies,
 * so a null becomes the literal `"null"`, which no real option matches: the
 * question renders perfectly and is unwinnable whatever the student picks,
 * with nothing on screen, in the console or in the boundary to say why. That
 * is worse than the crash, because nobody finds out.
 *
 * Filtered at the source rather than guarded at each use. A `?.` at the map
 * would stop the crash and leave a question on screen with no answers to pick,
 * which is a dead end a student cannot get past; dropping it here means the
 * only rows that reach the render are ones that can be answered *and* won. If
 * that leaves none, the existing "No Questions Available" state is already the
 * right thing to say.
 */
function isAnswerable(q) {
  return Boolean(
    q
    && Array.isArray(q.options) && q.options.length > 0
    // Not `!!q.correct_answer`: 0 and "" are falsy and a question whose answer
    // is genuinely "0" is ordinary in a maths bank.
    && q.correct_answer !== null && q.correct_answer !== undefined)
}

export default function Practice() {
  const [session, setSession]   = useState(null)
  const [questions, setQuestions] = useState([])
  const [index, setIndex]       = useState(0)
  const [loading, setLoading]   = useState(true)
  const [selected, setSelected] = useState(null)
  const [selectedAnswer, setSelectedAnswer] = useState(null)
  const [revealed, setRevealed] = useState(false)
  const [score, setScore]       = useState(0)
  const [finished, setFinished] = useState(false)
  const [timeLeft, setTimeLeft] = useState(TIMER)
  // Whether the session could be started at all. Three states, not two: a
  // failed start used to leave `questions` empty and fall through to the "No
  // Questions Available" screen, which tells a student the question bank is
  // empty when the truth is that the request did not come back.
  const [failed, setFailed] = useState(false)
  const timerRef = useRef(null)

  // `useCallback` on both of these, and declared before the effect that uses
  // them: the effect can then depend on `init` honestly rather than carry an
  // empty array and a suppressed warning. Neither closes over anything but
  // state setters, which React keeps stable, so the identities never change
  // and the effect still runs once.
  const startSession = useCallback(async () => {
    setLoading(true)
    setFailed(false)
    // Back to question one, unanswered. The end-of-session "Try Again" button
    // reset these by hand and nothing else did, so every other way of starting
    // a session -- the first load, and now the retry on the failed screen --
    // inherited whatever the last one left behind. Resetting here covers all
    // three, and is the belt to the timer gate's braces above.
    setIndex(0)
    setScore(0)
    setSelected(null)
    setSelectedAnswer(null)
    setRevealed(false)
    try {
      const s  = await apiFetch('/api/sessions/start', { method: 'POST', body: { title: 'Practice Session' } })
      const qs = await apiFetch('/api/questions?limit=10')
      setSession(s)
      // Unanswerable rows dropped here rather than guarded at the render --
      // see `isAnswerable`.
      setQuestions((qs || []).filter(isAnswerable))
    } catch (err) {
      console.error('Failed to start the practice session:', err)
      // A banner rather than `alert()`. The native dialog blocks the whole tab
      // until it is dismissed, is unstyled next to every other message in this
      // app, and leaves nothing behind once clicked -- so the page underneath
      // still had to say something, and what it said was "No Questions
      // Available".
      setFailed(true)
    } finally {
      setLoading(false)
    }
  }, [])

  const init = useCallback(async () => {
    // Every path out of here has to clear `loading`. It did not: with no
    // session the old version simply returned, and `loading` -- which starts
    // true -- was never set false again, so the page sat on its spinner for
    // ever with no error, no timeout and nothing to click. A student could
    // only escape by reloading, and nothing on screen suggested that.
    setLoading(true)
    setFailed(false)
    try {
      const { data } = await supabase.auth.getSession()
      if (data?.session) {
        await startSession()
        return
      }
      // Signed out between the guard admitting them and this running -- an
      // expired token, usually. Not a crash, but not a session either.
      setFailed(true)
      setLoading(false)
    } catch (e) {
      console.error('Failed to read the session:', e)
      setFailed(true)
      setLoading(false)
    }
  }, [startSession])

  useEffect(() => { init() }, [init])

  useEffect(() => {
    // Gated on there actually being a question to time, not just on the page
    // having stopped loading. `loading || finished` left the countdown running
    // over the failed-start screen and the empty state, where there is nothing
    // to answer: sixty seconds there called `handleTimeout`, which set
    // `revealed` on a question that was not on screen. Nothing reset it, so the
    // next successful start rendered question one already revealed, with every
    // option `disabled={revealed}` -- correct-looking and unanswerable.
    if (loading || finished || failed || !questions.length) return
    setTimeLeft(TIMER)
    clearInterval(timerRef.current)
    timerRef.current = setInterval(() => {
      setTimeLeft(t => {
        if (t <= 1) { clearInterval(timerRef.current); handleTimeout(); return 0 }
        return t - 1
      })
    }, 1000)
    return () => clearInterval(timerRef.current)
  }, [index, loading, finished, failed, questions.length])

  function normalize(val) {
  if (Array.isArray(val)) return val.join(', ')
  return String(val).trim()
  }

  /** Decide whether the pick was right, and hand it to `recordAnswer`.
   *
   * Correctness stays here because it is page-specific -- this page's questions
   * carry `options`, the adaptive page's carry `answer_options`, and the two
   * compare differently. Everything after that (the POST, the failure toast,
   * never throwing) is shared, which is what stopped the two pages disagreeing
   * about whether a lost answer is worth mentioning.
   */
  async function postAnswer(q, idx) {
    if (!q) return

    const selectedVal = idx >= 0 ? q.options[idx] : null
    const isCorrect = selectedVal !== null && normalize(selectedVal) === normalize(q.correct_answer)

    await recordAnswer({
      sessionId: session?.id,
      questionId: q.id,
      selectedIndex: idx,
      correct: isCorrect,
    })
  }

  function handleTimeout() {
    if (revealed) return
    setRevealed(true)
    if (session) postAnswer(questions[index], -1)
  }

  async function handleSelect(idx) {
    if (revealed) return
    clearInterval(timerRef.current)
    setSelected(idx)
    setRevealed(true)
    const q = questions[index]
    setSelectedAnswer(q.options[idx])
    const selectedVal = q.options[idx]
    if (normalize(selectedVal) === normalize(q.correct_answer)) {
      setScore(s => s + 1)
    }
    // if (idx === q.correct_index) setScore(s => s + 1)
    await postAnswer(q, idx)
  }

  async function handleNext() {
    if (index + 1 >= questions.length) {
      if (session) await endSession(session.id)
      setFinished(true)
    } else {
      setIndex(i => i + 1)
      setSelected(null)
      setSelectedAnswer(null)
      setRevealed(false)
    }
  }

  if (loading) return (
    <div className="min-h-[60vh] flex items-center justify-center">
      <motion.div animate={{ rotate: 360 }} transition={{ duration: 1, repeat: Infinity, ease: 'linear' }}
        className="w-12 h-12 border-4 border-indigo-600 border-t-transparent rounded-full" />
    </div>
  )

  // Before the empty state, and that order is the whole point: a failed start
  // leaves `questions` empty too, so checking length first told a student the
  // question bank was empty whenever the backend was simply unreachable.
  if (failed) return (
    <div className="max-w-lg mx-auto px-4 py-8">
      <LoadError what="this practice session" onRetry={init} />
    </div>
  )

  if (!questions.length) return (
    <div className="max-w-lg mx-auto px-4 py-16 text-center">
      <div className="text-6xl mb-4">📭</div>
      <h2 className="text-2xl font-black text-gray-900 dark:text-white mb-2">No Questions Available</h2>
      <p className="text-gray-500 dark:text-gray-400 mb-6">The AI backend generates these. Try the Adaptive mode instead.</p>
      <Link to="/adaptive" className="inline-block bg-indigo-600 text-white px-6 py-3 rounded-xl font-bold hover:bg-indigo-700 transition">
        Try AI Adaptive →
      </Link>
    </div>
  )

  if (finished) {
    const finalAcc = Math.round((score / questions.length) * 100)
    return (
      <div className="max-w-lg mx-auto px-4 py-16 text-center">
        <motion.div initial={{ scale: 0 }} animate={{ scale: 1 }} transition={{ type: 'spring', stiffness: 150 }}>
          <div className="text-7xl mb-4">🏆</div>
          <h2 className="text-3xl font-black text-gray-900 dark:text-white mb-1">Session Complete!</h2>
          <p className="text-gray-500 dark:text-gray-400 mb-6">You scored {score} out of {questions.length}</p>
          <div className="bg-white dark:bg-gray-900 rounded-2xl border border-gray-100 dark:border-gray-800 p-6 mb-8 shadow-sm">
            <p className="text-5xl font-black text-indigo-600 mb-1">{finalAcc}%</p>
            <p className="text-gray-500 dark:text-gray-400 text-sm">
              {finalAcc >= 80 ? '🔥 Outstanding!' : finalAcc >= 50 ? '👍 Good effort!' : '💪 Keep practicing!'}
            </p>
            <div className="mt-4 bg-gray-100 dark:bg-gray-800 rounded-full h-3 overflow-hidden">
              <motion.div className="h-full bg-gradient-to-r from-indigo-500 to-violet-500 rounded-full"
                initial={{ width: 0 }} animate={{ width: `${finalAcc}%` }} transition={{ duration: 0.8, delay: 0.3 }} />
            </div>
          </div>
          <div className="flex gap-3 justify-center">
            <button onClick={() => { setFinished(false); setIndex(0); setScore(0); setSelected(null); setSelectedAnswer(null); setRevealed(false); startSession() }}
              className="px-6 py-3 bg-indigo-600 text-white rounded-xl font-bold hover:bg-indigo-700 transition">
              Try Again
            </button>
            <Link to="/dashboard" className="px-6 py-3 bg-gray-100 dark:bg-gray-800 text-gray-700 dark:text-gray-200 rounded-xl font-bold hover:bg-gray-200 dark:hover:bg-gray-700 transition">
              Dashboard
            </Link>
          </div>
        </motion.div>
      </div>
    )
  }

  const q = questions[index]
  const timerPct = (timeLeft / TIMER) * 100

  return (
    <div className="max-w-2xl mx-auto px-4 py-8">
      {/* progress */}
      <div className="mb-6">
        <div className="flex items-center justify-between text-sm mb-2">
          <span className="text-gray-500 dark:text-gray-400">Question {index + 1} of {questions.length}</span>
          <span className={`font-bold tabular-nums ${timeLeft <= 10 ? 'text-rose-500 animate-pulse' : 'text-gray-700 dark:text-gray-300'}`}>⏱ {timeLeft}s</span>
        </div>
        <div className="h-2 bg-gray-100 dark:bg-gray-800 rounded-full overflow-hidden mb-1">
          <div className="h-full bg-gradient-to-r from-indigo-500 to-violet-500 rounded-full transition-all duration-300"
            style={{ width: `${((index) / questions.length) * 100}%` }} />
        </div>
        <div className="h-1 bg-gray-100 dark:bg-gray-800 rounded-full overflow-hidden">
          <div className={`h-full rounded-full transition-all duration-1000 ${timeLeft > 20 ? 'bg-green-500' : timeLeft > 10 ? 'bg-amber-500' : 'bg-rose-500'}`}
            style={{ width: `${timerPct}%` }} />
        </div>
      </div>

      {/* question card */}
      <div className="bg-white dark:bg-gray-900 rounded-2xl shadow-sm border border-gray-100 dark:border-gray-800 p-7">
        <div className="flex gap-2 mb-4 flex-wrap">
          {q.subject && (
            <span className="text-xs font-bold px-2.5 py-1 bg-indigo-100 dark:bg-indigo-900/40 text-indigo-700 dark:text-indigo-300 rounded-full capitalize">{q.subject}</span>
          )}
          {q.difficulty && (
            <span className={`text-xs font-bold px-2.5 py-1 rounded-full capitalize ${q.difficulty === 'easy' ? 'bg-green-100 text-green-700 dark:bg-green-900/40 dark:text-green-300' : q.difficulty === 'hard' ? 'bg-rose-100 text-rose-700 dark:bg-rose-900/40 dark:text-rose-300' : 'bg-amber-100 text-amber-700 dark:bg-amber-900/40 dark:text-amber-300'}`}>
              {q.difficulty}
            </span>
          )}
        </div>

        <p className="text-lg font-semibold text-gray-900 dark:text-white mb-6 leading-relaxed">{q.question_text}</p>

        <div className="space-y-3">
          {q.options.map((opt, i) => {
            let style = 'border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-800 text-gray-700 dark:text-gray-200 hover:border-indigo-300'
            const isCorrectOption = normalize(opt) === normalize(q.correct_answer)
            if (revealed) {
              // if (i === q.correct_index) style = 'border-green-400 bg-green-50 dark:bg-green-900/30 text-green-800 dark:text-green-200'
              if (isCorrectOption) style = 'border-green-400 bg-green-50 dark:bg-green-900/30 text-green-800 dark:text-green-200'
              else if (i === selected)   style = 'border-rose-400 bg-rose-50 dark:bg-rose-900/30 text-rose-800 dark:text-rose-200'
              else style = 'border-gray-100 dark:border-gray-700 opacity-50'
            } else if (selected === i) {
              style = 'border-indigo-400 bg-indigo-50 dark:bg-indigo-900/30 text-indigo-800 dark:text-indigo-200'
            }
            return (
              <motion.button key={i} onClick={() => handleSelect(i)} disabled={revealed}
                whileHover={!revealed ? { x: 4 } : {}}
                className={`w-full text-left p-4 rounded-xl border-2 transition-all duration-200 flex items-center gap-3 ${style}`}
              >
                <span className="w-7 h-7 flex-shrink-0 rounded-lg bg-white dark:bg-gray-700 border border-gray-200 dark:border-gray-600 flex items-center justify-center text-sm font-bold text-gray-600 dark:text-gray-300">
                  {String.fromCharCode(65 + i)}
                </span>
                <span>{Array.isArray(opt) ? opt.join(', ') : opt}</span>
                {/* {revealed && i === q.correct_index && <span className="ml-auto text-green-500 text-lg">✓</span>}
                {revealed && i === selected && i !== q.correct_index && <span className="ml-auto text-rose-500 text-lg">✗</span>} */}
                {revealed && normalize(opt) === normalize(q.correct_answer) && (
                <span className="ml-auto text-green-500 text-lg">✓</span>
                )}
                {revealed && selected === i && !isCorrectOption && (
                <span className="ml-auto text-rose-500 text-lg">✗</span>
                )}
              </motion.button>
            )
          })}
        </div>

        {revealed && (
          <motion.div initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} className="mt-6 flex justify-end">
            <button onClick={handleNext}
              className="px-6 py-2.5 bg-gradient-to-r from-indigo-600 to-violet-600 text-white rounded-xl font-bold hover:from-indigo-700 hover:to-violet-700 transition shadow">
              {index + 1 >= questions.length ? 'See Results →' : 'Next →'}
            </button>
          </motion.div>
        )}
      </div>
    </div>
  )
}