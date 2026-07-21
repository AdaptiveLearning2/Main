import { useState, useEffect, useRef } from 'react'
import { motion } from 'framer-motion'
import { useAuth } from '../../context/AuthContext'
import { supabase } from '../../lib/supabase'
import { apiFetch } from '../../lib/api'
import { createSignalRecorder, eegHealth, eegStatus } from '../../lib/signals'
import { GraduationCap, User, Minus, Plus, Sparkles, Brain } from 'lucide-react'

const EEG_DEBUG = import.meta.env.VITE_EEG_DEBUG === 'true'

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
  const [headband, setHeadband]   = useState({
    available: false, connected: false, samples: 0, lastTs: null,
    phase: 'idle', // idle | starting | scanning | connecting | connected
    deviceName: null,
  })

  // Dev-only EEG debug panel
  const [eegDebug, setEegDebug]       = useState(null)
  const [debugOpen, setDebugOpen]     = useState(true)
  const debugTimer  = useRef(null)
  const phaseTimer  = useRef(null)

  // load profile default grade + classes
  useEffect(() => {
    apiFetch('/api/profile/me').then(p => { if (p?.grade_level) setGrade(p.grade_level) }).catch(()=>{})
    apiFetch('/api/classes').then(c => {
      setClasses(c || [])
      if ((c || []).length && !classId) setClassId(c[0].id)
    }).catch(()=>{})
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // Check EEG health independently so the button isn't stuck unavailable if session start fails
  useEffect(() => {
    let alive = true
    const checkHealth = async () => {
      try {
        const h = await eegHealth()
        if (alive) setHeadband(s => ({ ...s, available: !!h.available }))
      } catch { if (alive) setHeadband(s => ({ ...s, available: false })) }
    }
    checkHealth()
    const id = setInterval(checkHealth, 5000)
    return () => { alive = false; clearInterval(id) }
  }, [])

  // start a session
  useEffect(() => {
    let r
    ;(async () => {
      try {
        const session = await apiFetch('/api/sessions/start', { method: 'POST', body: { title: 'Adaptive Session' } })
        setSessionId(session.id)
        r = createSignalRecorder({ sessionId: session.id })
        setRecorder(r)
        window.AL_currentSessionId = session.id
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

  // Poll EEG debug snapshot (dev only)
  useEffect(() => {
    if (!EEG_DEBUG) return
    const poll = async () => {
      try {
        const d = await apiFetch('/api/eeg/debug')
        setEegDebug(d)
      } catch { setEegDebug(null) }
    }
    poll()
    debugTimer.current = setInterval(poll, 1500)
    return () => clearInterval(debugTimer.current)
  }, [])

  useEffect(() => {
    if (!user?.id) return
    localStorage.setItem(`accuracyStats_${user.id}`, JSON.stringify(accuracyStats))
  }, [accuracyStats, user])

  useEffect(() => { localStorage.setItem('adaptive_mode', mode) }, [mode])

  const toggleHeadband = async () => {
    if (!recorder || !sessionId) {
      alert('Session not ready yet — wait 2 seconds and try again.')
      return
    }

    // — Disconnect —
    if (headband.connected) {
      clearTimeout(phaseTimer.current)
      await recorder.stop()
      setHeadband(s => ({ ...s, connected: false, phase: 'idle', deviceName: null }))
      return
    }

    // Safety: if something goes wrong and we never reach catch, reset after 30s
    clearTimeout(phaseTimer.current)
    phaseTimer.current = setTimeout(() => {
      setHeadband(s => s.phase !== 'idle' && s.phase !== 'connected'
        ? { ...s, phase: 'idle', deviceName: null }
        : s)
    }, 30000)

    try {
      // 1. Start the backend EEG poller
      setHeadband(s => ({ ...s, phase: 'starting' }))
      const res = await recorder.start()
      if (!res?.ok && !res?.running) throw new Error('Could not start EEG session')

      // 2. Disconnect any previous session so the headband isn't stuck in streaming state
      //    (causes BadStateError on the next connect if skipped)
      await apiFetch('/api/eeg/muse/disconnect', { method: 'POST' }).catch(() => {})
      await new Promise(r => setTimeout(r, 1500))

      // 3. Tell the native bridge to scan for nearby headbands
      setHeadband(s => ({ ...s, phase: 'scanning' }))
      await apiFetch('/api/eeg/muse/refresh', { method: 'POST' })

      // 4. Poll up to 12 s for at least one device to appear
      let devices = []
      let bluetoothEnabled = true
      for (let i = 0; i < 12; i++) {
        await new Promise(r => setTimeout(r, 1000))
        const s = await eegStatus()
        devices = s?.muse?.ingestion?.muse_devices || []
        if (devices.length > 0) break
        // Bridge reports the PC's Bluetooth radio state directly (see
        // MuseBridgeService::bluetooth_enabled) — stop waiting immediately
        // instead of burning the full 12 s when the radio itself is off.
        if (s?.muse?.ingestion?.bluetooth_enabled === false) {
          bluetoothEnabled = false
          break
        }
      }

      if (devices.length === 0) {
        setHeadband(s => ({ ...s, phase: 'idle' }))
        alert(
          bluetoothEnabled === false
            ? 'Bluetooth is turned off on this PC.\n\nTurn on Bluetooth in Windows Settings, then click Connect Headband again.'
            : 'No Muse headband found after 12 s.\n\n• Make sure the headband is turned on\n• Keep it within 1 m of your computer\n• Bluetooth must be enabled on your PC'
        )
        return
      }

      // 5. Connect to first discovered device (usually there's only one)
      const target = devices[0]
      setHeadband(s => ({ ...s, phase: 'connecting', deviceName: target }))
      await apiFetch('/api/eeg/muse/connect', { method: 'POST', body: { name: target } })

      // 6. Poll for actual BLE connection — bridge connects asynchronously.
      //    If we get BadStateError the headband is still streaming from a prior session;
      //    the user must power-cycle the headband in that case.
      let muse_ok = false
      for (let i = 0; i < 10; i++) {
        await new Promise(r => setTimeout(r, 1000))
        const s = await eegStatus()
        if (s?.muse?.ingestion?.muse_connected) { muse_ok = true; break }
      }

      if (!muse_ok) {
        setHeadband(s => ({ ...s, phase: 'idle', deviceName: null }))
        alert(
          'Headband found but connection failed (BadStateError).\n\n' +
          'The headband firmware is stuck in streaming mode.\n\n' +
          'Fix: hold the power button until it powers OFF (descending beeps), ' +
          'wait 10 seconds, power back ON, then click Connect Headband again.'
        )
        return
      }

      setHeadband(s => ({ ...s, connected: true, phase: 'connected' }))

    } catch (e) {
      console.error('[headband]', e)
      clearTimeout(phaseTimer.current)
      setHeadband(s => ({ ...s, phase: 'idle', deviceName: null }))
      alert('Headband connection failed: ' + (e.message || String(e)))
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
      if (sessionId) params.set('session_id', sessionId)

      const json = await apiFetch(`/api/generate-question?${params.toString()}`)
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
            {headband.phase === 'scanning'   && '🔍 Scanning for Muse headbands via Bluetooth...'}
            {headband.phase === 'connecting' && `🔗 Connecting to ${headband.deviceName || 'headband'}...`}
            {headband.phase === 'starting'   && 'Starting EEG session...'}
            {headband.phase === 'connected'  && `${headband.samples} samples sent · teacher can see your focus & stress live`}
            {headband.phase === 'idle' && (
              headband.connected
                ? `${headband.samples} samples sent · teacher can see your focus & stress live`
                : headband.available
                  ? 'EEG service ready. Turn on your Muse S headband then click Connect.'
                  : 'EEG service not reachable on port 8001. Make sure the EEGResearch backend is running.'
            )}
          </p>
        </div>
        <button onClick={toggleHeadband}
          disabled={!headband.available || !sessionId || ['starting','scanning','connecting'].includes(headband.phase)}
          className={`px-4 py-2 rounded-xl text-sm font-bold transition shadow disabled:opacity-50 disabled:cursor-not-allowed ${
            headband.connected ? 'bg-rose-500 hover:bg-rose-600 text-white' : 'bg-indigo-600 hover:bg-indigo-700 text-white'
          }`}>
          { headband.phase === 'starting'   ? 'Starting...'
          : headband.phase === 'scanning'   ? 'Scanning...'
          : headband.phase === 'connecting' ? 'Connecting...'
          : headband.connected              ? 'Disconnect'
          :                                   'Connect Headband' }
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
                  {data.eeg_adjusted && data.eeg_label === 'stressed' && (
                    <span className="text-xs font-bold px-2.5 py-1 bg-amber-100 dark:bg-amber-900/40 text-amber-700 dark:text-amber-300 rounded-full">
                      😰 EEG: eased difficulty
                    </span>
                  )}
                  {data.eeg_adjusted && data.eeg_label === 'focused' && (
                    <span className="text-xs font-bold px-2.5 py-1 bg-violet-100 dark:bg-violet-900/40 text-violet-700 dark:text-violet-300 rounded-full">
                      ⚡ EEG: raised difficulty
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

      {/* ── DEV-ONLY EEG DEBUG PANEL ───────────────────────────────────── */}
      {EEG_DEBUG && (
        <div className="mt-6 border border-dashed border-indigo-300 dark:border-indigo-700 rounded-2xl overflow-hidden text-xs font-mono">
          <button
            onClick={() => setDebugOpen(o => !o)}
            className="w-full flex items-center justify-between px-4 py-2 bg-indigo-50 dark:bg-indigo-950/50 text-indigo-700 dark:text-indigo-300 font-bold text-[11px]">
            <span>🛠 EEG Debug Panel (dev only — set VITE_EEG_DEBUG=false to hide)</span>
            <span>{debugOpen ? '▲' : '▼'}</span>
          </button>

          {debugOpen && (
            <div className="p-4 bg-gray-950 text-green-400 space-y-3">
              {!eegDebug || !eegDebug.available ? (
                <p className="text-red-400">⚠ EEGResearch not reachable on port 8001. Start it with: <span className="text-yellow-300">uvicorn src.app.main:app --port 8001</span></p>
              ) : (() => {
                const snap    = eegDebug.snapshot
                const muse    = eegDebug.muse    || {}
                const state   = snap?.state      || {}
                const feat    = snap?.features   || {}
                const bands   = snap?.bands      || {}
                const policy  = snap?.question_policy || {}
                const ing     = muse?.ingestion  || snap?.ingestion || {}
                const museSvcRunning = muse?.running
                // Scores are only meaningful when the electrodes are actually
                // reading the scalp. "poor" means libMuse reports the fit or the
                // data itself as bad -- focus/calm are still computed from that
                // garbage and look like confident numbers (a headband with two
                // dead electrodes happily reports "84.8% focus"), so blank them
                // out the same way a total signal loss is blanked.
                // Blank the scores only when we know they're untrustworthy:
                // no data at all, or the headband itself reporting bad
                // electrodes. A "poor" from the legacy calm-based heuristic
                // (older bridge with no contact data) reports poor for any
                // focused student, so blanking on that would hide every score.
                const untrusted = feat.signal_quality === 'no_signal' ||
                  (feat.signal_quality === 'poor' && feat.quality_basis === 'contact')

                const pct = v => v == null ? '—' : `${Math.round(typeof v === 'number' && v > 1 ? v : v * 100)}%`
                const bar = (v, color) => {
                  const w = v == null ? 0 : Math.round(typeof v === 'number' && v > 1 ? v : v * 100)
                  return (
                    <div className="flex items-center gap-2">
                      <div className="flex-1 h-2 bg-gray-800 rounded-full overflow-hidden">
                        <div className={`h-full rounded-full ${color}`} style={{ width: `${w}%` }} />
                      </div>
                      <span className="w-8 text-right">{pct(v)}</span>
                    </div>
                  )
                }

                const stateColor = { focused: 'text-green-400', stressed: 'text-red-400', neutral: 'text-yellow-400', insufficient_signal: 'text-gray-500' }
                const actionColor = { increase_difficulty: 'text-green-400', decrease_difficulty: 'text-red-400', reinforce_topic: 'text-yellow-400', fallback_default: 'text-gray-500' }

                return (
                  <>
                    {/* Row 0 — pipeline status */}
                    <div className="border border-gray-700 rounded p-2 space-y-1">
                      <p className="text-gray-500 text-[10px] uppercase tracking-widest mb-1">Pipeline</p>
                      <div className="flex flex-wrap gap-x-6 gap-y-1">
                        <span><span className="text-green-400">✓</span> EEGResearch :8001</span>
                        <span className={museSvcRunning ? 'text-green-400' : 'text-yellow-400'}>
                          {museSvcRunning ? '✓' : '○'} Session{museSvcRunning ? ' running' : ' not started — click Connect Headband'}
                        </span>
                        <span className={ing.bluetooth_enabled === false ? 'text-red-400' : 'text-green-400'}>
                          {ing.bluetooth_enabled === false ? '✗' : '✓'} Bluetooth radio{ing.bluetooth_enabled === false ? ' — turn on in Windows Settings' : ' on'}
                        </span>
                        <span className={ing.muse_connected ? 'text-green-400' : 'text-red-400'}>
                          {ing.muse_connected ? '✓' : '✗'} Native bridge :8765{!ing.muse_connected ? ' — run muse_native_bridge.exe' : ''}
                        </span>
                        <span className={ing.muse_connected ? 'text-green-400' : 'text-red-400'}>
                          {ing.muse_connected ? '✓' : '✗'} Headband BT{ing.muse_connected ? ` (${ing.active_muse_name || 'connected'})` : ' — not paired'}
                        </span>
                        <span className={snap ? 'text-green-400' : 'text-gray-500'}>
                          {snap ? '✓' : '○'} EEG samples flowing
                        </span>
                      </div>
                    </div>

                    {/* Row 1 — connection */}
                    <div className="flex flex-wrap gap-4">
                      <div>
                        <p className="text-gray-500 mb-1">EEG Source</p>
                        <p className="text-white">{ing.eeg_source || '—'}</p>
                      </div>
                      <div>
                        <p className="text-gray-500 mb-1">Bridge Mode</p>
                        <p className="text-white">{ing.bridge_mode || '—'}</p>
                      </div>
                      <div>
                        <p className="text-gray-500 mb-1">Signal Quality</p>
                        <p className={{ good: 'text-green-400', degraded: 'text-yellow-400', poor: 'text-red-400' }[feat.signal_quality] || 'text-gray-400'}>
                          {feat.signal_quality || (snap ? 'no data' : museSvcRunning ? 'waiting for bridge' : 'no session')}
                        </p>
                      </div>
                      <div>
                        <p className="text-gray-500 mb-1">Learner State</p>
                        <p className={stateColor[state.label] || 'text-gray-400'}>{state.label || '—'}</p>
                      </div>
                      <div>
                        <p className="text-gray-500 mb-1">Next Question</p>
                        <p className={actionColor[policy.action] || 'text-gray-400'}>{policy.action || '—'} {policy.difficulty != null ? `(lvl ${policy.difficulty})` : ''}</p>
                      </div>
                    </div>

                    {/* Row 2 — scores */}
                    <div className="grid grid-cols-2 gap-3">
                      <div>
                        <p className="text-gray-500 mb-1">Focus <span className="text-white">{untrusted ? '—' : pct(feat.focus_score)}</span></p>
                        {bar(untrusted ? null : feat.focus_score, 'bg-blue-500')}
                      </div>
                      <div>
                        <p className="text-gray-500 mb-1">Calm <span className="text-white">{untrusted ? '—' : pct(feat.calm_score)}</span></p>
                        {bar(untrusted ? null : feat.calm_score, 'bg-emerald-500')}
                      </div>
                      <div>
                        <p className="text-gray-500 mb-1">Confidence <span className="text-white">{untrusted ? '—' : pct(feat.confidence)}</span></p>
                        {bar(untrusted ? null : feat.confidence, 'bg-violet-500')}
                      </div>
                      <div>
                        <p className="text-gray-500 mb-1">Stress (derived) <span className="text-white">{feat.calm_score != null && !untrusted ? pct(1 - (feat.calm_score > 1 ? feat.calm_score / 100 : feat.calm_score)) : '—'}</span></p>
                        {bar(feat.calm_score != null && !untrusted ? 1 - (feat.calm_score > 1 ? feat.calm_score / 100 : feat.calm_score) : null, 'bg-red-500')}
                      </div>
                    </div>

                    {/* Row 3 — bands */}
                    {Object.keys(bands).length > 0 && (
                      <div>
                        <p className="text-gray-500 mb-1">EEG Bands</p>
                        <div className="flex flex-wrap gap-x-4 gap-y-1">
                          {['delta','theta','alpha','beta','gamma'].map(b => (
                            <span key={b} className="text-white">{b}: <span className="text-yellow-300">{bands[b] != null ? bands[b].toFixed(3) : '—'}</span></span>
                          ))}
                        </div>
                      </div>
                    )}

                    {/* Row 4 — raw state reason */}
                    {state.reason && (
                      <p className="text-gray-500">Reason: <span className="text-white">{state.reason}</span></p>
                    )}
                  </>
                )
              })()}
            </div>
          )}
        </div>
      )}
    </div>
  )
}