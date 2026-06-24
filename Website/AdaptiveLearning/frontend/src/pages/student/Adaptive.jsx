import { useState, useEffect } from 'react'
import { motion } from 'framer-motion'
import { useAuth } from '../../context/AuthContext'
import { supabase } from '../../lib/supabase'
import { apiFetch } from '../../lib/api'
import { createSignalRecorder, eegHealth, eegStatus } from '../../lib/signals'
import { GraduationCap, User, Minus, Plus, Sparkles, Brain } from 'lucide-react'

const TOPICS = ['ordering','rationals','expressions','algebra','geometry','angle_relationships','mean','median','mode','probability']
const ICONS  = { ordering:'🔢', rationals:'➗', expressions:'📐', algebra:'🔣', geometry:'📏', angle_relationships:'📐', mean:'〰️', median:'📊', mode:'🔁', probability:'🎲' }
const SHORT  = { angle_relationships: 'Angle Rel.' }
const GRADES = ['1st Grade','2nd Grade','3rd Grade','4th Grade','5th Grade','6th Grade','7th Grade','8th Grade','Highschool','College']

const initSubjects = () => {
  const s = {}; TOPICS.forEach(t => { s[t] = { correct: 0, attempts: 0 } }); return s
}

export default function Adaptive() {
  const { user } = useAuth()

  // mode: 'solo' (pick your own grade) | 'class' (use class grade)
  const [mode, setMode] = useState(() => localStorage.getItem('adaptive_mode') || 'solo')
  const [grade, setGrade] = useState('1st Grade')
  const [classes, setClasses] = useState([])
  const [classId, setClassId] = useState('')
  const [bias, setBias] = useState(0) // -1 easier, 0 auto, +1 harder

  const [accuracyStats, setAccuracyStats] = useState(() => {
    if (!user?.id) return { total: { correct: 0, attempts: 0 }, subjects: initSubjects() }
    const saved = localStorage.getItem(`accuracyStats_${user.id}`)
    return saved ? JSON.parse(saved) : { total: { correct: 0, attempts: 0 }, subjects: initSubjects() }
  })

  const [data, setData]             = useState(null)
  const [phase, setPhase]           = useState('idle')
  const [selectedAnswer, setSelectedAnswer] = useState(null)
  const [activeButton, setActiveButton]     = useState(null)
  const [correct, setCorrect]       = useState(false)
  const [sessionCount, setSessionCount] = useState(0)
  const [error, setError]           = useState(false)

  // EEG headband state
  const [recorder, setRecorder]   = useState(null)
  const [sessionId, setSessionId] = useState(null)
  const [headband, setHeadband]   = useState({ available: false, connected: false, samples: 0, lastTs: null })

  // load profile default grade + classes
  useEffect(() => {
    apiFetch('/api/profile/me').then(p => { if (p?.grade_level) setGrade(p.grade_level) }).catch(()=>{})
    apiFetch('/api/classes').then(c => {
      setClasses(c || [])
      if ((c || []).length && !classId) setClassId(c[0].id)
    }).catch(()=>{})
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // start a session + check EEG service
  useEffect(() => {
    let r
    ;(async () => {
      try {
        const session = await apiFetch('/api/sessions/start', { method: 'POST', body: { title: 'Adaptive Session' } })
        setSessionId(session.id)
        r = createSignalRecorder({ sessionId: session.id })
        setRecorder(r)
        window.AL_currentSessionId = session.id
        const h = await eegHealth()
        setHeadband(s => ({ ...s, available: !!h.available }))
      } catch {}
    })()
    return () => { r?.stop() }
  }, [])

  // poll EEG status while connected
  useEffect(() => {
    if (!sessionId) return
    let killed = false
    const tick = async () => {
      const s = await eegStatus()
      if (killed) return
      setHeadband(prev => ({
        ...prev,
        available: !!s.service,
        connected: !!s.poller?.running,
        samples:   s.poller?.samples || 0,
        lastTs:    s.poller?.last_ts || null,
      }))
    }
    tick()
    const id = setInterval(tick, 3000)
    return () => { killed = true; clearInterval(id) }
  }, [sessionId])

  useEffect(() => {
    if (!user?.id) return
    localStorage.setItem(`accuracyStats_${user.id}`, JSON.stringify(accuracyStats))
  }, [accuracyStats, user])

  useEffect(() => { localStorage.setItem('adaptive_mode', mode) }, [mode])

  const toggleHeadband = async () => {
    console.log('[headband] toggle clicked', { recorder: !!recorder, sessionId, headband })
    if (!recorder) {
      alert('No recorder yet — sessionId might still be loading. Wait 2s and retry.')
      return
    }
    if (!sessionId) {
      alert('No sessionId — the practice session never started. Refresh the page.')
      return
    }
    if (headband.connected) {
      console.log('[headband] stopping')
      await recorder.stop()
      setHeadband(s => ({ ...s, connected: false }))
    } else {
      console.log('[headband] starting…')
      const res = await recorder.start()
      console.log('[headband] start result:', res)
      if (res?.running) setHeadband(s => ({ ...s, connected: true }))
      else alert('Could not start headband: ' + JSON.stringify(res))
    }
  }

  const sendAccuracyToBackend = async () => {
    const { data: topicRows, error: topicError } = await supabase.from('math_topics').select('id, topic_name')
    if (topicError) { console.error(topicError); return }
    const topicMap = {}
    topicRows.forEach(t => { topicMap[t.topic_name] = t.id })
    const rows = Object.entries(accuracyStats.subjects).map(([name, vals]) => ({
      user_id: user.id,
      topic_id: topicMap[name],
      correct_questions: Number(vals.correct) || null,
      attempted_questions: Number(vals.attempts) || null,
    }))
    const { error } = await supabase.from('user_math_performance').upsert(rows, { onConflict: 'user_id,topic_id' })
    if (error) console.error(error)
  }

  const fetchQuestion = async () => {
    setPhase('loading'); setError(false)
    try {
      await sendAccuracyToBackend()
      const params = new URLSearchParams({ user_id: user.id, bias: String(bias) })
      if (mode === 'class' && classId) params.set('class_id', classId)
      else                              params.set('grade', grade)

      const res  = await fetch(`http://localhost:8000/api/generate-question?${params.toString()}`, {
        headers: { 'Authorization': `Bearer ${user.access_token}` }
      })
      const json = await res.json()
      if (!json?.question_text) throw new Error('Invalid response')
      setData(json)
      setActiveButton(null); setSelectedAnswer(null)
      setPhase('question')
    } catch (err) {
      console.error(err); setError(true); setPhase('idle')
    }
  }

  const handleSubmit = () => {
    const isCorrect = JSON.stringify(data.answer_options[selectedAnswer]) === JSON.stringify(data.correct_answer)
    setCorrect(isCorrect)
    setSessionCount(n => n + 1)
    setAccuracyStats(prev => {
      const n = JSON.parse(JSON.stringify(prev))
      const topic = data.question_topic
      n.total.attempts += 1
      if (n.subjects[topic]) n.subjects[topic].attempts += 1
      if (isCorrect) {
        n.total.correct += 1
        if (n.subjects[topic]) n.subjects[topic].correct += 1
      }
      return n
    })
    setPhase('result')
  }

  const getAcc = (topic) => {
    const s = accuracyStats.subjects[topic]
    if (!s || s.attempts === 0) return null
    return Math.round((s.correct / s.attempts) * 100)
  }
  const totalAcc = accuracyStats.total.attempts > 0
    ? Math.round((accuracyStats.total.correct / accuracyStats.total.attempts) * 100) : null

  const activeClass = classes.find(c => c.id === classId)
  const effectiveGrade = mode === 'class' ? (activeClass?.grade_level || '—') : grade
  const biasLabel = bias === -1 ? 'Easier' : bias === 1 ? 'Harder' : 'Auto'

  return (
    <div className="p-6 lg:p-8 pb-12">
      <motion.div initial={{ opacity: 0, y: -10 }} animate={{ opacity: 1, y: 0 }} className="mb-4">
        <h1 className="text-3xl font-black text-gray-900 dark:text-white">🧠 AI Adaptive Practice</h1>
        <p className="text-gray-500 dark:text-gray-400 mt-1">The AI picks your weakest topic and generates a custom question.</p>
      </motion.div>

      {/* HEADBAND PANEL */}
      <motion.div initial={{ opacity: 0, y: -6 }} animate={{ opacity: 1, y: 0 }}
        className="mb-4 bg-white dark:bg-gray-900 border border-gray-100 dark:border-gray-800 rounded-2xl p-4 shadow-sm flex items-center gap-3 flex-wrap">
        <div className={`w-10 h-10 rounded-xl flex items-center justify-center text-white shadow ${
          headband.connected
            ? 'bg-gradient-to-br from-emerald-500 to-green-600 animate-pulse'
            : headband.available
              ? 'bg-gradient-to-br from-indigo-500 to-violet-600'
              : 'bg-gradient-to-br from-gray-400 to-gray-500'
        }`}>
          <Brain size={18} />
        </div>
        <div className="flex-1 min-w-0">
          <p className="text-sm font-black text-gray-900 dark:text-white flex items-center gap-2">
            Muse Headband
            {headband.connected && <span className="text-[10px] font-bold px-2 py-0.5 bg-emerald-100 dark:bg-emerald-900/40 text-emerald-700 dark:text-emerald-300 rounded-full">● STREAMING</span>}
            {!headband.connected && headband.available && <span className="text-[10px] font-bold px-2 py-0.5 bg-indigo-100 dark:bg-indigo-900/40 text-indigo-700 dark:text-indigo-300 rounded-full">ready</span>}
            {!headband.available && <span className="text-[10px] font-bold px-2 py-0.5 bg-gray-100 dark:bg-gray-800 text-gray-500 rounded-full">offline</span>}
          </p>
          <p className="text-[11px] text-gray-400 mt-0.5">
            {headband.connected
              ? `${headband.samples} samples sent · teacher can see your focus & stress live`
              : headband.available
                ? 'EEG service detected on port 8001. Click connect to share live focus & stress with your teacher.'
                : 'EEG service not reachable on port 8001. Make sure the EEGResearch backend is running.'}
          </p>
        </div>
        <button onClick={toggleHeadband}
          disabled={!headband.available || !sessionId}
          className={`px-4 py-2 rounded-xl text-sm font-bold transition shadow disabled:opacity-50 disabled:cursor-not-allowed ${
            headband.connected ? 'bg-rose-500 hover:bg-rose-600 text-white' : 'bg-indigo-600 hover:bg-indigo-700 text-white'
          }`}>
          {headband.connected ? 'Disconnect' : 'Connect Headband'}
        </button>
      </motion.div>

      <div className="grid lg:grid-cols-3 gap-6">
        <div className="lg:col-span-2 space-y-4">
          {sessionCount > 0 && (
            <div className="flex gap-3 flex-wrap">
              <div className="bg-white dark:bg-gray-900 rounded-xl border border-gray-100 dark:border-gray-800 px-4 py-2 flex items-center gap-2 shadow-sm">
                <span className="text-sm font-bold text-gray-700 dark:text-gray-300">📝 {sessionCount} answered</span>
              </div>
              {totalAcc !== null && (
                <div className="bg-white dark:bg-gray-900 rounded-xl border border-gray-100 dark:border-gray-800 px-4 py-2 flex items-center gap-2 shadow-sm">
                  <span className={`text-sm font-bold ${totalAcc >= 70 ? 'text-green-600' : totalAcc >= 40 ? 'text-amber-600' : 'text-rose-600'}`}>
                    🎯 {totalAcc}% accuracy
                  </span>
                </div>
              )}
            </div>
          )}

          <div className="bg-white dark:bg-gray-900 rounded-2xl border border-gray-100 dark:border-gray-800 shadow-sm overflow-hidden">
            {phase === 'idle' && (
              <div className="p-8 lg:p-10">
                <motion.div animate={{ y: [0, -8, 0] }} transition={{ duration: 2, repeat: Infinity, ease: 'easeInOut' }}
                  className="text-6xl mb-4 text-center">🚀</motion.div>
                <h2 className="text-xl font-black text-gray-900 dark:text-white mb-2 text-center">Ready to practice?</h2>
                <p className="text-gray-500 dark:text-gray-400 text-sm mb-6 max-w-sm mx-auto text-center">
                  The AI analyses your performance across 10 topics and picks the one you need most.
                </p>

                {/* Mode toggle */}
                <div className="max-w-md mx-auto mb-5">
                  <div className="grid grid-cols-2 gap-2 bg-gray-100 dark:bg-gray-800 rounded-xl p-1">
                    <button onClick={() => setMode('solo')}
                      className={`py-2 rounded-lg text-sm font-bold flex items-center justify-center gap-2 transition ${mode === 'solo' ? 'bg-white dark:bg-gray-900 text-indigo-600 shadow-sm' : 'text-gray-500'}`}>
                      <User size={14} /> Solo
                    </button>
                    <button onClick={() => setMode('class')}
                      className={`py-2 rounded-lg text-sm font-bold flex items-center justify-center gap-2 transition ${mode === 'class' ? 'bg-white dark:bg-gray-900 text-indigo-600 shadow-sm' : 'text-gray-500'}`}
                      disabled={classes.length === 0}>
                      <GraduationCap size={14} /> Class
                    </button>
                  </div>
                </div>

                {/* Grade / class picker */}
                <div className="max-w-md mx-auto mb-5">
                  {mode === 'solo' ? (
                    <>
                      <label className="block text-sm font-bold text-gray-700 dark:text-gray-300 mb-2 text-center">Grade Level</label>
                      <select value={grade} onChange={e => setGrade(e.target.value)}
                        className="w-full text-center px-4 py-3 rounded-xl border border-gray-300 dark:border-gray-700 bg-white dark:bg-gray-900 text-gray-800 dark:text-gray-200 text-sm">
                        {GRADES.map(d => <option key={d} value={d}>{d}</option>)}
                      </select>
                    </>
                  ) : classes.length === 0 ? (
                    <p className="text-center text-sm text-gray-500">Join a class first to use class mode.</p>
                  ) : (
                    <>
                      <label className="block text-sm font-bold text-gray-700 dark:text-gray-300 mb-2 text-center">Class</label>
                      <select value={classId} onChange={e => setClassId(e.target.value)}
                        className="w-full text-center px-4 py-3 rounded-xl border border-gray-300 dark:border-gray-700 bg-white dark:bg-gray-900 text-gray-800 dark:text-gray-200 text-sm">
                        {classes.map(c => (
                          <option key={c.id} value={c.id}>
                            {c.name}{c.grade_level ? ` — ${c.grade_level}` : ''}
                          </option>
                        ))}
                      </select>
                      {activeClass && !activeClass.grade_level && (
                        <p className="text-xs text-amber-600 mt-2 text-center">⚠️ Teacher hasn't set this class's grade yet — using AI default.</p>
                      )}
                    </>
                  )}
                </div>

                {/* Bias control */}
                <div className="max-w-md mx-auto mb-6">
                  <label className="block text-sm font-bold text-gray-700 dark:text-gray-300 mb-2 text-center">Difficulty</label>
                  <div className="flex items-center justify-center gap-2">
                    <button onClick={() => setBias(-1)}
                      className={`flex items-center gap-1 px-4 py-2 rounded-xl text-sm font-bold transition border ${bias === -1 ? 'bg-emerald-500 text-white border-emerald-500 shadow' : 'border-gray-200 dark:border-gray-700 text-gray-600 dark:text-gray-300 hover:border-emerald-400'}`}>
                      <Minus size={14} /> Easier
                    </button>
                    <button onClick={() => setBias(0)}
                      className={`flex items-center gap-1 px-4 py-2 rounded-xl text-sm font-bold transition border ${bias === 0 ? 'bg-indigo-600 text-white border-indigo-600 shadow' : 'border-gray-200 dark:border-gray-700 text-gray-600 dark:text-gray-300 hover:border-indigo-400'}`}>
                      <Sparkles size={14} /> Auto
                    </button>
                    <button onClick={() => setBias(1)}
                      className={`flex items-center gap-1 px-4 py-2 rounded-xl text-sm font-bold transition border ${bias === 1 ? 'bg-rose-500 text-white border-rose-500 shadow' : 'border-gray-200 dark:border-gray-700 text-gray-600 dark:text-gray-300 hover:border-rose-400'}`}>
                      <Plus size={14} /> Harder
                    </button>
                  </div>
                  <p className="text-[11px] text-gray-400 mt-2 text-center">
                    Generating <strong>{biasLabel}</strong> questions for <strong>{effectiveGrade}</strong>
                  </p>
                </div>

                <div className="text-center">
                  <motion.button onClick={fetchQuestion}
                    disabled={mode === 'class' && !classId}
                    whileHover={{ scale: 1.03 }} whileTap={{ scale: 0.97 }}
                    className="px-8 py-3 bg-gradient-to-r from-indigo-600 to-violet-600 text-white rounded-xl font-bold shadow-lg hover:from-indigo-700 hover:to-violet-700 transition disabled:opacity-50">
                    Generate Question
                  </motion.button>
                  {error && <p className="text-rose-500 text-sm mt-4">⚠️ Generation failed — try again.</p>}
                </div>
              </div>
            )}

            {phase === 'loading' && (
              <div className="p-10 text-center">
                <motion.div animate={{ rotate: 360 }} transition={{ duration: 1, repeat: Infinity, ease: 'linear' }}
                  className="w-12 h-12 border-4 border-indigo-600 border-t-transparent rounded-full mx-auto mb-4" />
                <p className="text-gray-500 dark:text-gray-400">AI is picking your topic...</p>
              </div>
            )}

            {(phase === 'question' || phase === 'result') && data && (
              <div className="p-7">
                <div className="flex gap-2 mb-4 flex-wrap">
                  {data.question_topic && (
                    <span className="text-xs font-bold px-2.5 py-1 bg-indigo-100 dark:bg-indigo-900/40 text-indigo-700 dark:text-indigo-300 rounded-full capitalize flex items-center gap-1">
                      {ICONS[data.question_topic]} {data.question_topic.replace('_', ' ')}
                    </span>
                  )}
                  {data.difficulty && (
                    <span className={`text-xs font-bold px-2.5 py-1 rounded-full capitalize ${data.difficulty === 'easy' ? 'bg-green-100 text-green-700 dark:bg-green-900/40 dark:text-green-300' : data.difficulty === 'medium' ? 'bg-amber-100 text-amber-700 dark:bg-amber-900/40 dark:text-amber-300' : 'bg-rose-100 text-rose-700 dark:bg-rose-900/40 dark:text-rose-300'}`}>
                      {data.difficulty}
                    </span>
                  )}
                  {data.effective_grade && (
                    <span className="text-xs font-bold px-2.5 py-1 bg-violet-100 dark:bg-violet-900/40 text-violet-700 dark:text-violet-300 rounded-full flex items-center gap-1">
                      <GraduationCap size={11} /> {data.effective_grade}
                    </span>
                  )}
                  {headband.connected && (
                    <span className="text-xs font-bold px-2.5 py-1 bg-emerald-100 dark:bg-emerald-900/40 text-emerald-700 dark:text-emerald-300 rounded-full flex items-center gap-1">
                      <Brain size={11} /> Live EEG
                    </span>
                  )}
                </div>

                <p className="text-lg font-semibold text-gray-900 dark:text-white mb-6 leading-relaxed">{data.question_text}</p>

                <div className="space-y-3 mb-6">
                  {data.answer_options?.map((opt, i) => {
                    const isSelected = activeButton === i
                    const isResult   = phase === 'result'
                    const isCorrectOpt = isResult && JSON.stringify(opt) === JSON.stringify(data.correct_answer)
                    const isWrong      = isResult && isSelected && !isCorrectOpt

                    let style = 'border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-800 text-gray-700 dark:text-gray-200 hover:border-indigo-300'
                    if (isResult) {
                      if (isCorrectOpt) style = 'border-green-400 bg-green-50 dark:bg-green-900/30 text-green-800 dark:text-green-200'
                      else if (isWrong)  style = 'border-rose-400 bg-rose-50 dark:bg-rose-900/30 text-rose-800 dark:text-rose-200'
                      else style = 'border-gray-100 dark:border-gray-700 opacity-50'
                    } else if (isSelected) {
                      style = 'border-indigo-400 bg-indigo-50 dark:bg-indigo-900/30 text-indigo-800 dark:text-indigo-200'
                    }
                    return (
                      <motion.button key={i} onClick={() => { if (phase !== 'question') return; setSelectedAnswer(i); setActiveButton(i) }}
                        disabled={phase === 'result'}
                        whileHover={phase === 'question' ? { x: 4 } : {}}
                        className={`w-full text-left p-4 rounded-xl border-2 transition-all duration-200 flex items-center gap-3 ${style}`}>
                        <span className="w-7 h-7 flex-shrink-0 rounded-lg bg-white dark:bg-gray-700 border border-gray-200 dark:border-gray-600 flex items-center justify-center text-sm font-bold">
                          {String.fromCharCode(65 + i)}
                        </span>
                        <span>{Array.isArray(opt) ? opt.join(', ') : opt}</span>
                        {isResult && isCorrectOpt && <span className="ml-auto text-green-500 text-lg">✓</span>}
                        {isResult && isWrong      && <span className="ml-auto text-rose-500 text-lg">✗</span>}
                      </motion.button>
                    )
                  })}
                </div>

                {phase === 'question' && (
                  <button onClick={handleSubmit} disabled={selectedAnswer === null}
                    className="w-full py-3 bg-gradient-to-r from-indigo-600 to-violet-600 text-white rounded-xl font-bold disabled:opacity-50 disabled:cursor-not-allowed hover:from-indigo-700 hover:to-violet-700 transition shadow">
                    Submit Answer
                  </button>
                )}

                {phase === 'result' && (
                  <motion.div initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} className="space-y-3">
                    <div className={`p-4 rounded-xl text-center font-black text-lg ${correct ? 'bg-green-50 dark:bg-green-900/30 text-green-700 dark:text-green-300' : 'bg-rose-50 dark:bg-rose-900/30 text-rose-700 dark:text-rose-300'}`}>
                      {correct ? '🎉 Correct! Great job!' : '❌ Not quite — keep going!'}
                    </div>
                    <div className="flex items-center justify-center gap-2">
                      <span className="text-xs text-gray-400">Next question:</span>
                      <button onClick={() => setBias(-1)} className={`text-xs px-3 py-1.5 rounded-lg font-bold border ${bias === -1 ? 'bg-emerald-500 text-white border-emerald-500' : 'border-gray-200 dark:border-gray-700 text-gray-600 dark:text-gray-300'}`}>Easier</button>
                      <button onClick={() => setBias(0)}  className={`text-xs px-3 py-1.5 rounded-lg font-bold border ${bias === 0 ? 'bg-indigo-600 text-white border-indigo-600' : 'border-gray-200 dark:border-gray-700 text-gray-600 dark:text-gray-300'}`}>Auto</button>
                      <button onClick={() => setBias(1)}  className={`text-xs px-3 py-1.5 rounded-lg font-bold border ${bias === 1 ? 'bg-rose-500 text-white border-rose-500' : 'border-gray-200 dark:border-gray-700 text-gray-600 dark:text-gray-300'}`}>Harder</button>
                    </div>
                    <motion.button onClick={fetchQuestion}
                      whileHover={{ scale: 1.01 }} whileTap={{ scale: 0.98 }}
                      className="w-full py-3 bg-gradient-to-r from-indigo-600 to-violet-600 text-white rounded-xl font-bold hover:from-indigo-700 hover:to-violet-700 transition shadow">
                      Next Question →
                    </motion.button>
                  </motion.div>
                )}
              </div>
            )}
          </div>
        </div>

        {/* accuracy sidebar */}
        <div className="bg-white dark:bg-gray-900 rounded-2xl border border-gray-100 dark:border-gray-800 p-5 shadow-sm h-fit">
          <h3 className="font-black text-gray-900 dark:text-white mb-4">Topic Accuracy</h3>
          <div className="space-y-3">
            {TOPICS.map(topic => {
              const acc = getAcc(topic)
              const s   = accuracyStats.subjects[topic]
              return (
                <div key={topic}>
                  <div className="flex items-center justify-between mb-1">
                    <span className="text-xs font-medium text-gray-600 dark:text-gray-400 flex items-center gap-1">
                      {ICONS[topic]} {SHORT[topic] || topic.replace('_', ' ')}
                    </span>
                    <span className={`text-xs font-black ${acc === null ? 'text-gray-400' : acc >= 70 ? 'text-green-600 dark:text-green-400' : acc >= 40 ? 'text-amber-600 dark:text-amber-400' : 'text-rose-600 dark:text-rose-400'}`}>
                      {acc === null ? '—' : `${acc}%`}
                    </span>
                  </div>
                  <div className="h-1.5 bg-gray-100 dark:bg-gray-700 rounded-full overflow-hidden">
                    <motion.div
                      className={`h-full rounded-full ${acc === null ? '' : acc >= 70 ? 'bg-green-500' : acc >= 40 ? 'bg-amber-500' : 'bg-rose-500'}`}
                      initial={{ width: 0 }}
                      animate={{ width: acc ? `${acc}%` : '0%' }}
                      transition={{ duration: 0.5 }}
                    />
                  </div>
                  {s?.attempts > 0 && <p className="text-[10px] text-gray-400 mt-0.5">{s.correct}/{s.attempts} correct</p>}
                </div>
              )
            })}
          </div>

          {sessionCount > 0 && (
            <button
              onClick={() => {
                setAccuracyStats({ total: { correct: 0, attempts: 0 }, subjects: initSubjects() })
                setSessionCount(0)
                localStorage.removeItem(`accuracyStats_${user.id}`)
              }}
              className="mt-5 w-full text-xs text-gray-400 hover:text-rose-500 transition py-2"
            >
              Reset stats
            </button>
          )}
        </div>
      </div>
    </div>
  )
}