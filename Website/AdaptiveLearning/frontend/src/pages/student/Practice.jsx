import { useEffect, useState, useRef } from 'react'
import { Link } from 'react-router-dom'
import { motion } from 'framer-motion'
import { apiFetch } from '../../lib/api'
import { supabase } from '../../lib/supabase'

const TIMER = 60

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
  const timerRef = useRef(null)

  // useEffect(() => { startSession() }, [])
  useEffect(() => {
    async function init() {
      const { data } = await supabase.auth.getSession()
      if (data.session) {
        startSession()
      }
    }
    init()
  }, [])

  useEffect(() => {
    if (loading || finished) return
    setTimeLeft(TIMER)
    clearInterval(timerRef.current)
    timerRef.current = setInterval(() => {
      setTimeLeft(t => {
        if (t <= 1) { clearInterval(timerRef.current); handleTimeout(); return 0 }
        return t - 1
      })
    }, 1000)
    return () => clearInterval(timerRef.current)
  }, [index, loading, finished])

  async function startSession() {
    setLoading(true)
    try {
      const s  = await apiFetch('/api/sessions/start', { method: 'POST', body: { title: 'Practice Session' } })
      const qs = await apiFetch('/api/questions?limit=10')
      setSession(s)
      setQuestions(qs || [])
    } catch (err) {
      alert('Failed to start session: ' + err.message)
    } finally {
      setLoading(false)
    }
  }

  function normalize(val) {
  if (Array.isArray(val)) return val.join(', ')
  return String(val).trim()
  }

  async function postAnswer(q, idx) {
    if (!session) return

    const selectedVal = idx >= 0 ? q.options[idx] : null
    const isCorrect = selectedVal !== null && normalize(selectedVal) === normalize(q.options[q.correct_index])
    
    await apiFetch(`/api/sessions/${session.id}/answer`, {
      method: 'POST',
      body: { question_id: q.id, selected_index: idx, correct: isCorrect }
    })
    // try {
    //   await apiFetch(`/api/sessions/${session.id}/answer`, {
    //     method: 'POST',
    //     body: { question_id: q.id, selected_index: idx, correct: idx === q.correct_index }
    //   })
    // } catch {}
  }

  async function endSession(id) {
    try { await apiFetch(`/api/sessions/${id}/end`, { method: 'POST' }) } catch {}
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
    if (normalize(selectedVal) === normalize(q.options[q.correct_index])) {
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