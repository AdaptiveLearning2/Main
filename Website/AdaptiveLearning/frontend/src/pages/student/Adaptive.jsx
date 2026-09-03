import { useState, useEffect, useRef, useCallback } from 'react'
import { motion } from 'framer-motion'
import { useAuth } from '../../context/AuthContext'
import { supabase } from '../../lib/supabase'
import { apiFetch } from '../../lib/api'
import { endSession, recordAnswer } from '../../lib/session'
import { createSignalRecorder, eegHealth, eegStatus, eegDevices } from '../../lib/signals'
import { startPush, stopPush, stopPushOnUnload, pushStatus,
         deviceStart, deviceStop, museRefresh, museConnect,
         museDisconnect, museState, devices as sidecarDevices,
         releasePushIfIdle, sidecarDebug
       } from '../../lib/sidecar'
import RecordingIndicator from '../../components/signals/RecordingIndicator'
import { GraduationCap, User, Minus, Plus, Sparkles, Brain, BatteryFull, BatteryLow, Clock } from 'lucide-react'
import { toast } from 'sonner'
import QuestionFigure from '../../components/questions/QuestionFigure'
import { TOPICS as ALL_TOPICS, TOPIC_ICONS } from '../../lib/topics'
import { contactQuality } from '../../lib/contactQuality'

const EEG_DEBUG = import.meta.env.VITE_EEG_DEBUG === 'true'

// The page's own recovery of a headband that dropped mid-session, used only
// once the native bridge has given up on its own (or is too old to try).
// Each attempt is a full scan + connect, so three is already a minute or so
// of trying; past that the student is better told than kept waiting.
const RECONNECT_ATTEMPTS = 3
const RECONNECT_BACKOFF_MS = [2000, 4000, 8000]
// While a drop is being recovered the status is polled faster than the usual
// 5s, since this is the moment the student is watching the panel.
const RECONNECT_POLL_MS = 2000
// Contact readings are one frame every few seconds with no smoothing, so a
// single poor frame -- a head turn, a hand on the strap -- must not raise the
// hint. Two in a row is the sidecar's own smoothing, roughly.
const CONTACT_POOR_STREAK = 2

// Retry delay for offering the session to the sidecar. The student often
// opens the lesson before starting the local app, so this is normal, not an
// error -- long enough to avoid hammering the port, short enough to start
// recording soon.
const PUSH_RETRY_MS = 5000

// Labeled by the sensor a student recognizes (headband, camera), not by
// table name.
const CHANNEL_LABELS = [
  ['cognitive', 'Headband'],
  ['heart',     'Heart sensor'],
  ['face',      'Camera'],
]

const TOPICS = ALL_TOPICS
const ICONS  = TOPIC_ICONS
const SHORT  = { angle_relationships: 'Angle Rel.' }
const GRADES = ['1st Grade','2nd Grade','3rd Grade','4th Grade','5th Grade','6th Grade','7th Grade','8th Grade','Highschool','College']

/** Formats an error for a toast description, one place so every hardware
 * failure on this page shows the message the same way. */
const errorDetail = (e) => e?.message || String(e)

const initSubjects = () => {
  const s = {}; TOPICS.forEach(t => { s[t] = { correct: 0, attempts: 0 } }); return s
}

/**
 * Ingestion runs in one of two modes, reported by the backend as
 * `ingest_mode` and surfaced here as `headband.pushMode`. Pull: the
 * co-located backend polls the sidecar itself, and hardware control goes
 * through `/api/eeg/*`. Push: hosted backend, local sidecar -- this page
 * talks to the sidecar directly on loopback via `lib/sidecar.js`, and
 * `/api/eeg/*` refuses with 409. Comments below note only what's
 * mode-specific at each site.
 */
export default function Adaptive() {
  const { user } = useAuth()

  // mode: 'solo' (pick your own grade) | 'class' (use class grade)
  const [mode, setMode] = useState(() => localStorage.getItem('adaptive_mode') || 'solo')
  const [grade, setGrade] = useState('1st Grade')
  const [classes, setClasses] = useState([])
  const [classId, setClassId] = useState('')
  const [bias, setBias] = useState(0) // -1 easier, 0 auto, +1 harder

  // Planned session length, from the profile. Advisory only -- it asks
  // between questions instead of ending the session, since a timer could cut
  // off a question mid-answer. Null until the profile loads, so nothing is
  // timed against a guess.
  const [durationMin, setDurationMin]         = useState(null)
  // How many questions the student said they wanted this sitting, or null for
  // no limit. Per session rather than a saved preference: how long someone
  // wants to work varies by the afternoon, and `session_duration_minutes`
  // already covers the standing answer.
  const [questionGoal, setQuestionGoal]       = useState(null)
  const [goalDismissed, setGoalDismissed]     = useState(false)
  const [sessionStartedAt, setSessionStartedAt] = useState(null)
  const [elapsedMin, setElapsedMin]           = useState(0)
  const [timeUpDismissed, setTimeUpDismissed] = useState(false)
  const [finishing, setFinishing]             = useState(false)

  // Topic accuracy comes from `user_math_performance` on the server, not from
  // this browser -- a client-side cache could drift from the database and
  // wouldn't reflect a parent's erasure. `accuracyState` distinguishes a
  // failed read from a student who genuinely has no history.
  const [accuracyStats, setAccuracyStats] = useState(
    () => ({ total: { correct: 0, attempts: 0 }, subjects: initSubjects() }))
  const [accuracyState, setAccuracyState] = useState('loading')  // loading | ready | failed

  // Keyed on the user *id*, not the user object -- the same rule as the
  // profile read in AuthContext. The effect below re-runs whenever this
  // callback changes, and a `user` object that is recreated (a token refresh,
  // or a test's `useAuth` mock returning a fresh literal per call) would
  // otherwise re-fetch the whole performance table on every render: measured
  // at 172 requests in 7s under the reconnect tests, once per status tick.
  const uid = user?.id
  const loadAccuracy = useCallback(async () => {
    if (!uid) return
    // Same endpoint StudentProgressReport uses, so there's one reader of this
    // table and one access check to keep correct.
    let rows
    try {
      rows = await apiFetch(`/api/performance/student/${uid}`)
    } catch (e) {
      console.error('[accuracy] could not load topic performance', e)
      setAccuracyState('failed')
      return
    }
    const subjects = initSubjects()
    let correct = 0, attempts = 0
    for (const r of rows || []) {
      const name = r.math_topics?.topic_name
      const c = r.correct_questions || 0
      const a = r.attempted_questions || 0
      if (name && subjects[name]) subjects[name] = { correct: c, attempts: a }
      correct += c
      attempts += a
    }
    setAccuracyStats({ total: { correct, attempts }, subjects })
    setAccuracyState('ready')
  }, [uid])

  // Applies the +1 the backend already made, using the topic name it
  // returned -- avoids a full re-fetch of performance data after every
  // answer.
  const applyAttempt = useCallback((topic, wasCorrect) => {
    if (!topic) return                 // nothing was attributed; nothing moved
    // Shared bump logic so the tile and the running total can't disagree.
    const bump = ({ correct, attempts }) => ({
      correct:  correct  + (wasCorrect ? 1 : 0),
      attempts: attempts + 1,
    })
    setAccuracyStats(prev => {
      const prior = prev.subjects[topic]
      // Counts every attempt in the total even without a matching tile --
      // `TOPICS` is a fixed list and `math_topics` can grow, so an
      // unrecognized topic must still count toward the overall total.
      return {
        total: bump(prev.total),
        subjects: prior
          ? { ...prev.subjects, [topic]: bump(prior) }
          : prev.subjects,
      }
    })
  }, [])

  useEffect(() => { loadAccuracy() }, [loadAccuracy])

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
    // `pushMode` stays unset until a health check lands -- guessing "not
    // push" showed a false outage message on first paint.
    available: false, connected: false, samples: 0, lastTs: null,
    // `reconnecting` is a link that dropped on its own and is being brought
    // back -- by the bridge, or failing that by this page. `connected` is
    // false throughout it: the data is not flowing, and saying otherwise
    // would be the silent-drop problem wearing a different label.
    phase: 'idle', // idle | starting | scanning | connecting | connected | reconnecting
    deviceName: null,
    // Charge percent, or null for "no reading" (not connected, an old bridge
    // with no battery field, or no BATTERY packet yet). Must never render as
    // 0%, which is a real charge level.
    battery: null,
    // {attempt, max, byBridge} while reconnecting, else null. `attempt` is 0
    // while the bridge is still waiting out its first backoff.
    reconnect: null,
    // Electrode contact from the bridge's hsi/is_good, debounced. null until
    // measured -- "not reported" must not read as either fine or poor.
    contactPoor: null,
  })

  // Sidecar stations are registered EEG devices (e.g. multiple headband
  // rigs), chosen before the BLE scan/connect flow even starts -- distinct
  // from the BLE device name list (muse_devices) below.
  const [stations, setStations]     = useState([])
  // Tracked separately from `stations`, which is filtered to headbands only,
  // so the picker never offers a camera to pair.
  const [camera, setCamera]         = useState(
    { id: null, running: false, busy: false })
  const [stationId, setStationId]   = useState(null)

  // What the local sidecar reports about its own delivery, under push. Null
  // until asked, so "not running here" and "asked and down" stay distinguishable.
  const [push, setPush]               = useState(null)
  // Channels that actually delivered a reading since the last poll -- not
  // just consented ones, or the chip would claim a recording that stopped.
  const [recording, setRecording]     = useState([])
  // Last poll's cumulative counts, used to compute a delta -- the counts
  // only grow, so comparing to 0 would never clear.
  const lastRecorded = useRef(null)
  // Serialises start against stop. Both are async and the effect can tear down
  // while a start is still in flight, so they are chained rather than raced.
  const pushHandoff = useRef(Promise.resolve())
  // Read by `recover`, which is a stable callback and must not close over a
  // stale session id.
  const sessionIdRef = useRef(null)

  // Dev-only EEG debug panel
  const [eegDebug, setEegDebug]       = useState(null)
  const [debugOpen, setDebugOpen]     = useState(true)
  const debugTimer  = useRef(null)
  const phaseTimer  = useRef(null)
  // Mirrors of state for the polls and the reconnect loop, which run from
  // timers and would otherwise read the values they closed over at creation.
  const headbandRef = useRef(headband)
  const recorderRef = useRef(null)
  // False once the page is gone; `pairOnce` reads it between steps. Set true
  // in the effect body rather than at declaration so StrictMode's
  // mount/unmount/mount in development does not leave it false.
  const pageAlive = useRef(true)
  useEffect(() => {
    pageAlive.current = true
    return () => {
      pageAlive.current = false
      // The page-driven reconnect loop is its own token, so cancel it too:
      // left running it sends a disconnect per attempt to the one shared
      // bridge device -- tearing down a link the student may since have
      // re-paired from another page -- and toasts a failure on that page.
      if (reconnectRun.current) reconnectRun.current.cancelled = true
      reconnectRun.current = null
    }
  }, [])
  // The page-driven reconnect in progress, or null. A token object rather
  // than a boolean so a cancel reaches the loop that is actually running and
  // not one started after it.
  const reconnectRun = useRef(null)
  // Consecutive poor contact readings; the hint needs CONTACT_POOR_STREAK.
  const poorStreak = useRef(0)

  useEffect(() => { sessionIdRef.current = sessionId }, [sessionId])
  useEffect(() => { headbandRef.current = headband }, [headband])
  useEffect(() => { recorderRef.current = recorder }, [recorder])

  // Leaving the page ends the session, whatever it holds.
  //
  // Two problems, one call, and the two cases end differently because they
  // should. `toggleHeadband` has to create a session -- under
  // `INGEST_MODE=pull` the EEG reservation is scoped by `session_id`, so
  // connecting needs one to hang off -- and a student who paired a headband
  // and walked away left a 0-question "Adaptive Session" in History until the
  // 6h sweep collected it. That is what "it recorded a session I never
  // started" looks like from the outside. Ending it hands it to
  // `_discard_if_nothing_recorded`, which *deletes* a session that recorded
  // nothing, so the phantom disappears rather than gaining an end stamp.
  //
  // A session with answers is *closed* instead -- crediting lifetime totals,
  // writing the daily rollup, archiving the charts -- at the moment the
  // student leaves. Left open, it read `LIVE` on the teacher's screen with
  // its duration ticking up for up to six hours, telling a teacher a child
  // was working who had gone home.
  //
  // The cost, taken deliberately: navigating to History mid-lesson and coming
  // back starts a new session rather than resuming. Leaving the page is a
  // clear enough signal of being done, and a LIVE badge for a student who
  // left is the worse claim.
  //
  // Only covers leaving the page. A tab close still falls to the sweep --
  // `endSession` uses `apiFetch`, which does not outlive the document, and
  // the keepalive dance `stopPushOnUnload` does is not worth repeating for a
  // row the sweep already collects. That is the other half of why the teacher
  // badge is now activity-based rather than trusting `ended_at` alone.
  useEffect(() => () => {
    if (sessionIdRef.current) endSession(sessionIdRef.current)
  }, [])

  // Cleans up on unmount: `phaseTimer` is a 30s safety net that resets the
  // headband card if a connect attempt hangs, and must not fire after the
  // page is gone. `window.AL_currentSessionId` must not keep naming a
  // session that no longer exists.
  useEffect(() => () => {
    clearTimeout(phaseTimer.current)
    delete window.AL_currentSessionId
  }, [])

  // Clock starts when the session starts, not when the page opened --
  // otherwise idle time on the setup screen would count against the
  // student's planned duration.
  useEffect(() => {
    if (!sessionId) {
      setSessionStartedAt(null)
      setElapsedMin(0)
      setTimeUpDismissed(false)
      // Cleared here with the rest of the per-session state, not in
      // `finishSession`: this effect is what every path to "no session" goes
      // through. Left standing, one "Keep going" silenced the check-in for
      // every later session in the sitting -- and the picker still showed the
      // number selected, so a student had no reason to re-click it and no way
      // to tell the reminder had been switched off.
      //
      // `questionGoal` itself is deliberately kept, like `durationMin`: the
      // number they chose is their answer for the sitting, not for one
      // session.
      setGoalDismissed(false)
      return
    }
    setSessionStartedAt(prev => prev ?? Date.now())
  }, [sessionId])

  useEffect(() => {
    if (!sessionStartedAt || !durationMin) return
    // Checked every 20s -- precise enough for a duration reminder, and cheap
    // since the page is otherwise idle between questions.
    const tick = () => setElapsedMin((Date.now() - sessionStartedAt) / 60000)
    tick()
    const id = setInterval(tick, 20000)
    return () => clearInterval(id)
  }, [sessionStartedAt, durationMin])

  const timeUp = !!durationMin && !timeUpDismissed && elapsedMin >= durationMin

  // Reached, not exceeded: the count only moves when an answer is recorded, so
  // this can never fire part way through a question. Same asked-not-enforced
  // rule as `timeUp` -- see the banner below.
  const goalReached = !!questionGoal && !goalDismissed && sessionCount >= questionGoal

  // load profile default grade + classes
  useEffect(() => {
    apiFetch('/api/profile/me').then(p => {
      if (p?.grade_level) setGrade(p.grade_level)
      // `??`, not `||`: 0 is a valid bias choice (Auto), and `||` would
      // treat it as unset.
      if (p?.difficulty_bias != null) setBias(p.difficulty_bias)
      if (p?.session_duration_minutes != null) setDurationMin(p.session_duration_minutes)
    }).catch(()=>{})
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
        // Runs before a session exists, so pushMode is known before first
        // paint -- otherwise the page shows a false "not reachable" message
        // under push.
        if (alive) setHeadband(s => ({
          ...s,
          pushMode: h.ingest_mode === 'push',
          available: !!h.available,
        }))
      } catch { if (alive) setHeadband(s => ({ ...s, available: false })) }
    }
    checkHealth()
    const id = setInterval(checkHealth, 5000)
    return () => { alive = false; clearInterval(id) }
  }, [])

  // Discover sidecar stations once the EEG service is reachable. Auto-select when
  // there's exactly one; otherwise wait for the user to pick one via the picker below.
  useEffect(() => {
    // `available` is null (not false) when the backend hasn't probed the
    // sidecar, which is normal under push -- gating on falsiness alone would
    // skip the push branch below.
    if (!headband.available && !headband.pushMode) return
    let alive = true
    // Under push the backend can't reach the sidecar either, so ask it directly.
    const source = headband.pushMode
      ? sidecarDevices().then(list => ({ devices: list })).catch(() => ({ devices: [] }))
      : eegDevices()
    source.then(d => {
      if (!alive) return
      // Read before the headband filter below, so camera state doesn't
      // depend on the headband picker's rules.
      const face = (d?.devices || []).find(x => x.kind === 'face')
      setCamera(c => ({ ...c, id: face?.device_id || null,
                        running: !!face?.running }))
      // Cameras share the device registry with headbands, so they're
      // filtered out here -- otherwise this picker offers a camera as a
      // headband to connect, and breaks the single-device auto-select below.
      // Excludes `face` rather than allow-listing headband kinds, so a new
      // headband kind isn't silently dropped.
      const list = (d?.devices || []).filter(s => s.kind !== 'face')
      setStations(list)
      setStationId(prev => {
        if (prev && list.some(s => s.device_id === prev)) return prev
        if (list.length === 1) return list[0].device_id
        if (list.length === 0) return 'default'
        return null
      })
    })
    return () => { alive = false }
  }, [headband.available, headband.pushMode])

  // Re-offers the session to the sidecar. Shared by the initial handover and
  // the status poll, since both are the same failure seen at different
  // times.
  const recover = useCallback(() => {
    const sid = sessionIdRef.current
    if (!sid) return
    pushHandoff.current = pushHandoff.current
      .catch(() => {})
      .then(() => startPush(sid))
      .then(() => setPush(p => ({ ...(p || {}), running: true, reachable: true, error: null })))
      .catch(err => {
        // Same reasoning as the initial handover: a 409 is the sidecar
        // deliberately declining, and re-offering cannot change its mind.
        if (err?.status === 409) {
          setPush(p => ({ ...(p || {}), running: false, reachable: true, enabled: false }))
        }
      })
  }, [])

  // Polls the headband itself: charge, electrode contact, and whether the
  // BLE link is still up. In both modes -- under pull the backend's poller
  // keeps running through a drop, so `poller.running` never says the
  // headband went away; only the bridge's own `muse_connected` does.
  //
  // Keeps polling through `reconnecting`, which is the whole point: a drop
  // used to set `connected: false`, which ended this effect, so nothing was
  // left watching for the link to come back.
  const reconnecting = headband.phase === 'reconnecting'
  useEffect(() => {
    if (!(headband.connected || reconnecting) || !stationId) return
    let killed = false
    const read = async () => {
      try {
        const st = headband.pushMode ? await museState(stationId)
                                     : (await eegStatus(stationId))?.muse
        if (killed) return
        const ing = st?.ingestion || {}
        const prev = headbandRef.current
        const pct = ing.battery_percent
        // typeof check, not `pct || null` -- a flat 0% battery must not be
        // read as "no reading".
        const battery = typeof pct === 'number' ? pct : null

        // `=== false`, not falsiness: an absent field means the sidecar
        // didn't report, not that the headband went away. Same for `=== true`
        // on the way back.
        const linkUp = ing.muse_connected === true
        const dropped = ing.muse_connected === false

        if (prev.phase === 'reconnecting') {
          if (linkUp) {
            onReconnected()
            return
          }
          // Show the bridge's own progress while it is trying. Once it has
          // given up -- or never reported trying, which is an older bridge --
          // this page takes over.
          const bridgeTrying = ing.reconnecting === true
          if (bridgeTrying) {
            setHeadband(s => ({ ...s, reconnect: {
              attempt: ing.reconnect_attempt || 0,
              max: ing.reconnect_max_attempts || 0,
              byBridge: true,
            } }))
          } else if (!reconnectRun.current) {
            startFrontendReconnect()
          }
          return
        }

        // A drop is a link that was *up*, so this waits for `pairOnce` to
        // have finished (`phase: 'connected'`) rather than for `connected`,
        // which under pull is the backend's poller and is true from
        // `/api/eeg/start` -- before the scan has even begun. Keyed on
        // `connected` alone, every pull-mode pairing read as a drop the
        // moment it started: "The headband disconnected." over a headband
        // that had never connected, and 2s later this page's own loop sent a
        // second connect on top of the first, which the bridge honours by
        // disconnecting first. Measured on hardware: three clicks to pair.
        if (dropped && prev.connected && prev.phase === 'connected') {
          onDropped(ing)
          return
        }

        // Steady state: charge and contact. Contact is debounced -- one poor
        // frame is a head turn, two in a row is the strap.
        const quality = contactQuality(ing)
        if (quality === 'poor') poorStreak.current += 1
        else poorStreak.current = 0
        const contactPoor = quality == null ? null : poorStreak.current >= CONTACT_POOR_STREAK
        setHeadband(s => ({ ...s, battery, contactPoor }))
      } catch {
        // Leave the last known values on a failed read -- they're cleared on
        // disconnect instead, which is what actually invalidates them.
      }
    }
    read()
    const id = setInterval(read, reconnecting ? RECONNECT_POLL_MS : 5000)
    return () => { killed = true; clearInterval(id) }
    // onDropped/onReconnected/startFrontendReconnect read everything through
    // refs and setState, so they are stable in effect.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [headband.connected, reconnecting, headband.pushMode, stationId])

  // A link that went away on its own. Said out loud once, then watched: the
  // panel used to reset to "Connect Headband" as if nothing had been paired,
  // and a student mid-question had no reason to look at it.
  const onDropped = (ing) => {
    poorStreak.current = 0
    const byBridge = ing.reconnecting === true
    setHeadband(s => ({
      ...s, connected: false, phase: 'reconnecting', battery: null, contactPoor: null,
      reconnect: { attempt: ing.reconnect_attempt || 0, max: ing.reconnect_max_attempts || 0, byBridge },
    }))
    toast.warning('The headband disconnected.', {
      description: 'Trying to reconnect. Check it is switched on and sitting on your head.',
      duration: 8_000,
    })
    // Bridge reports no recovery of its own (older build, or it already
    // gave up): this page's loop starts now rather than on the next poll.
    if (!byBridge && !reconnectRun.current) startFrontendReconnect()
  }

  const onReconnected = () => {
    if (reconnectRun.current) reconnectRun.current.cancelled = true
    reconnectRun.current = null
    setHeadband(s => ({ ...s, connected: true, phase: 'connected', reconnect: null }))
    toast.success('Headband reconnected.')
  }

  // Scan + connect, up to RECONNECT_ATTEMPTS times with a growing wait. Only
  // reached once the bridge is not handling it -- two drivers retrying the
  // same headband would fight over the scan.
  const startFrontendReconnect = () => {
    if (reconnectRun.current) return
    const run = { cancelled: false }
    reconnectRun.current = run
    ;(async () => {
      const hw = makeHw(recorderRef.current)
      const sid = sessionIdRef.current
      let ok = false
      for (let attempt = 1; attempt <= RECONNECT_ATTEMPTS && !run.cancelled; attempt++) {
        setHeadband(s => ({ ...s, phase: 'reconnecting',
                             reconnect: { attempt, max: RECONNECT_ATTEMPTS, byBridge: false } }))
        await new Promise(r => setTimeout(r, RECONNECT_BACKOFF_MS[attempt - 1]))
        if (run.cancelled) break
        // The link may have come back on its own while waiting.
        const st = await hw.status().catch(() => null)
        if (run.cancelled) break
        if (st?.ingestion?.muse_connected === true) { ok = true; break }
        const res = await pairOnce(hw, sid, run).catch(() => ({ ok: false }))
        if (run.cancelled) break
        if (res.ok) { ok = true; break }
      }
      if (run.cancelled) return
      reconnectRun.current = null
      if (ok) {
        onReconnected()
        return
      }
      setHeadband(s => ({ ...s, connected: false, phase: 'idle', deviceName: null,
                           battery: null, reconnect: null, contactPoor: null }))
      toast.error('The headband could not be reconnected.', {
        description: 'Check it is switched on and charged, then click Connect Headband.',
        duration: 15_000,
      })
    })()
  }

  // "Stop trying". Goes through the same teardown as Disconnect so nothing is
  // left half-paired -- plus an explicit bridge disconnect first, because
  // Disconnect's teardown stops the sidecar's stream without sending the
  // bridge a command, and only a command cancels the bridge's own attempts.
  const cancelReconnect = async () => {
    if (reconnectRun.current) reconnectRun.current.cancelled = true
    reconnectRun.current = null
    const hw = makeHw(recorderRef.current)
    await hw.disconnect().catch(() => {})
    await disconnectHeadband(hw)
  }

  // Closes the current session. Clearing `sessionId` triggers the handover
  // effect's cleanup, which takes the student's token back off the sidecar.
  // Hardware stays paired -- finishing a session isn't unplugging the
  // headband.
  const finishSession = async () => {
    setFinishing(true)
    try {
      // The call itself lives in lib/session.js, shared with Practice.jsx,
      // so both pages report failures the same way.
      await endSession(sessionIdRef.current)
    } finally {
      setSessionId(null)
      setSessionCount(0)
      setData(null)
      setPhase('idle')
      setFinishing(false)
      // Cleared with the rest of session state, or it would keep naming a
      // finished session as if it were still live.
      delete window.AL_currentSessionId
    }
  }

  const creating = useRef(null)

  const getOrCreateSession = async () => {
    if (sessionId) return sessionId
    if (creating.current) return creating.current
    creating.current = apiFetch('/api/sessions/start', { method: 'POST', body: { title: 'Adaptive Session' } })
      .then(s => { setSessionId(s.id); return s.id })
      .finally(() => { creating.current = null })
    return creating.current
  }

  // poll EEG status while connected
  useEffect(() => {
    if (!sessionId) return
    let killed = false
    const tick = async () => {
      const s = await eegStatus(stationId)
      if (killed) return
      setHeadband(prev => ({
        ...prev,
        // `service` is null (not false) under push -- the backend never probes
        // a sidecar it has no route to.
        pushMode: s.ingest_mode === 'push',
        available: !!s.service,
        // Only under pull: `poller.running` is the backend's own poller, which
        // doesn't exist under push and would otherwise read as disconnected.
        // And not during a reconnect: the poller runs on through a BLE drop,
        // so `running` alone would flip the panel back to connected while the
        // bridge is still bringing the link back. The telemetry poll above
        // owns that transition -- and *only* it. Reading `muse_connected`
        // here as well raced it: this poll is faster, and setting
        // `connected: false` tore the telemetry effect down before it could
        // claim the drop, so under pull nothing was announced or recovered.
        ...(s.ingest_mode === 'push' || prev.phase === 'reconnecting' ? {} : {
          connected: !!s.poller?.running,
        }),
        samples:   s.poller?.samples || 0,
        lastTs:    s.poller?.last_ts || null,
        // Only under pull -- the telemetry effect above already polls this
        // under push. typeof check so a real 0% charge isn't read as no
        // reading.
        ...(s.ingest_mode === 'push' ? {} : {
          battery: typeof s.muse?.ingestion?.battery_percent === 'number'
            ? s.muse.ingestion.battery_percent : null,
        }),
      }))
    }
    tick()
    const id = setInterval(tick, 3000)
    return () => { killed = true; clearInterval(id) }
  }, [sessionId, stationId])

  // Hands the session and student's token to the sidecar under push, and
  // takes them back when the session ends. Pull already has the backend's
  // poller as writer; running both would double-write `cognitive_signals`,
  // which has no dedupe key.
  useEffect(() => {
    if (!sessionId || !headband.pushMode) return
    let killed = false

    // Retried rather than attempted once -- the sidecar often starts after
    // this page does.
    let attempt = null
    const handOver = () => {
      // Chained onto the same promise the cleanup's stop uses, so start and
      // stop stay ordered instead of racing.
      pushHandoff.current = pushHandoff.current
        .catch(() => {})
        .then(() => startPush(sessionId))
        .then(() => {
          if (!killed) setPush(p => ({ ...(p || {}), running: true, reachable: true, error: null }))
        })
        .catch(err => {
          if (killed) return
          if (err.status === 409) {
            // Not a failure: the sidecar is up but PUSH_ENABLED is off, so it
            // declines to be a second writer. Don't retry -- it can only ever
            // answer the same way.
            setPush(p => ({ ...(p || {}), running: false, reachable: true, enabled: false,
                            error: String(err.message || err) }))
            return
          }
          // An unreachable sidecar is the ordinary case on a machine with no
          // headband or camera running yet -- recorded so the panel can say why.
          setPush(p => ({ ...(p || {}), running: false, reachable: false, error: String(err.message || err) }))
          attempt = setTimeout(handOver, PUSH_RETRY_MS)
        })
      return pushHandoff.current
    }
    handOver()

    // Tokens expire roughly hourly and a lesson can run longer. Without
    // re-handing a fresh token, pushes start 401ing and samples pile up in
    // the bounded queue until dropped.
    const { data: sub } = supabase.auth.onAuthStateChange((event, session) => {
      if (event !== 'TOKEN_REFRESHED' || killed) return
      // Uses the token from the callback argument -- calling `getSession()`
      // here deadlocks on supabase-js's auth lock while it dispatches.
      // Chained with start/stop so teardown can't race a refresh.
      const token = session?.access_token
      if (!token) return
      pushHandoff.current = pushHandoff.current
        .catch(() => {})
        .then(() => (killed ? null : startPush(sessionId, token)))
        .catch(() => {})
    })

    // Effect cleanup doesn't run on a tab close or hard refresh. Without this
    // the sidecar keeps recording with the student's token for up to an hour
    // after they leave. `pagehide` (not `beforeunload`) also fires on
    // bfcache and is reliable on mobile.
    const onPageHide = () => { stopPushOnUnload() }
    window.addEventListener('pagehide', onPageHide)

    return () => {
      killed = true
      clearTimeout(attempt)
      window.removeEventListener('pagehide', onPageHide)
      sub?.subscription?.unsubscribe()
      // Chained behind any start in flight -- under StrictMode's teardown/remount,
      // a bare stopPush() could land after the remount's startPush.
      pushHandoff.current = pushHandoff.current
        .catch(() => {})
        .then(() => stopPush())
        .catch(() => {})
      setPush(null)
    }
  }, [sessionId, headband.pushMode])

  // Delivery counts for the panel and recording chip. Polled slower than the
  // status effect since only a human glances at it.
  useEffect(() => {
    if (!sessionId || !headband.pushMode) return
    let killed = false
    const tick = () => pushStatus()
      .then(d => {
        if (killed) return
        const prev = lastRecorded.current
        const now = d.recorded || {}
        // First poll establishes the baseline and claims nothing. Without this
        // a mid-session page reload would list every channel that had ever
        // recorded, including ones that stopped an hour ago.
        setRecording(prev ? CHANNEL_LABELS
          .filter(([key]) => (now[key] || 0) > (prev[key] || 0))
          .map(([, label]) => label) : [])
        lastRecorded.current = now
        // Recovers a sidecar that went away after handover -- the initial
        // retry has long since stopped, and a restarted sidecar has no token.
        // `enabled: false` is excluded: that's config, re-handing would just 409.
        if (d.enabled !== false && !d.running) recover()
        // Reachability and running are separate claims -- collapsing them
        // would show a disabled sidecar as healthy.
        setPush(p => ({ ...(p || {}), ...d, reachable: true, running: !!d.enabled && !!d.running }))
      })
      .catch(() => {
        if (killed) return
        setPush(p => ({ ...(p || {}), reachable: false, running: false }))
        // A chip claiming a recording that isn't happening is worse than none.
        setRecording([])
        // Next successful poll treats this as a fresh baseline, same as the
        // first poll after mount.
        lastRecorded.current = null
        recover()
      })
    tick()
    const id = setInterval(tick, 10000)
    return () => { killed = true; clearInterval(id) }
  }, [sessionId, headband.pushMode, recover])

  // Poll EEG debug snapshot (dev only)
  useEffect(() => {
    if (!EEG_DEBUG) return
    // Waits for pushMode to be known -- guessing "not push" too early would
    // misread the push response as an outage.
    if (headband.pushMode === undefined) return
    const poll = async () => {
      try {
        // Under push the backend has no route to the sidecar, so the page
        // reads it directly and feeds the panel the same shape.
        const d = headband.pushMode
          ? await sidecarDebug(stationId || 'default')
          : await apiFetch(`/api/eeg/debug${stationId ? `?device_id=${encodeURIComponent(stationId)}` : ''}`)
        setEegDebug(d)
      } catch { setEegDebug(null) }
    }
    poll()
    debugTimer.current = setInterval(poll, 1500)
    return () => clearInterval(debugTimer.current)
  }, [stationId, headband.pushMode])

  // Clears the old localStorage cache, which is no longer read but would
  // otherwise linger as a stale, misleading value on a shared computer.
  useEffect(() => {
    if (!user?.id) return
    localStorage.removeItem(`accuracyStats_${user.id}`)
  }, [user])

  useEffect(() => { localStorage.setItem('adaptive_mode', mode) }, [mode])

  // Stops one device, then releases the shared push client only if nothing
  // else is still streaming. The returned device list also updates camera
  // state, so the card can't claim to record after the client is torn down.
  const endPushDevice = async (deviceId) => {
    await deviceStop(deviceId).catch(() => {})
    const { devices: list } = await releasePushIfIdle()
    if (list) {
      const face = list.find(d => d.kind === 'face')
      setCamera(c => ({ ...c, running: !!face?.running }))
    } else {
      // The list could not be read, so the client was released to be safe --
      // which means nothing is being delivered, whatever is still capturing.
      setCamera(c => ({ ...c, running: false }))
    }
  }

  const toggleCamera = async () => {
    // Push only. Checked here too, not just via the disabled button -- the
    // button explains to the student, this guards correctness.
    if (!camera.id || !headband.pushMode || camera.busy) return
    setCamera(c => ({ ...c, busy: true }))
    try {
      if (camera.running) {
        // Uses the same helper as the headband, or turning off the camera
        // alone would leave the sidecar holding the student's token.
        await endPushDevice(camera.id)
      } else {
        // Turning the camera on doesn't start a session -- frames are
        // captured and dropped until a lesson actually starts consuming
        // them.
        await deviceStart(camera.id)
        setCamera(c => ({ ...c, running: true }))
      }
    } catch (e) {
      console.error('[camera]', e)
      toast.error('The camera could not be switched.', {
        description: errorDetail(e),
      })
    } finally {
      setCamera(c => ({ ...c, busy: false }))
    }
  }

  // Under pull the backend proxies scan/connect via /api/eeg/muse/*; under
  // push those refuse (409) and the page talks to the sidecar on loopback
  // directly, admitted by `require_local_controller`.
  //
  // Takes the recorder as an argument rather than reading state, so the
  // pull branch can close over one created moments ago and not yet rendered.
  const makeHw = (rec) => headband.pushMode ? {
    // Brings the hardware up only -- delivery starts separately via the
    // `sessionId` effect once a session exists, not when the headband
    // connects.
    begin:      async () => { await deviceStart(stationId)
                              return { ok: true, running: true } },
    disconnect: () => museDisconnect(stationId),
    scan:       () => museRefresh(stationId),
    connect:    (name) => museConnect(name, stationId),
    status:     () => museState(stationId),
    // Not `stopPush()` outright: that's global to the sidecar and would
    // also tear down a running camera's delivery.
    end:        () => endPushDevice(stationId),
  } : {
    // Stream up, nothing written: recording is armed by the first question
    // (`armRecording`), the same split push has with `startPush`.
    begin:      () => rec.start({ record: false }),
    disconnect: () => apiFetch('/api/eeg/muse/disconnect',
                               { method: 'POST', body: { device_id: stationId } }),
    scan:       (sid) => apiFetch('/api/eeg/muse/refresh',
                                  { method: 'POST', body: { device_id: stationId, session_id: sid } }),
    connect:    (name, sid) => apiFetch('/api/eeg/muse/connect',
                                        { method: 'POST', body: { name, device_id: stationId, session_id: sid } }),
    status:     async () => (await eegStatus(stationId))?.muse || {},
    // `?.` because the page-driven reconnect can end a session whose recorder
    // was already dropped by a Disconnect that raced it.
    end:        () => rec?.stop(),
  }

  // One scan-and-connect. Shared by the Connect button and the reconnect
  // loop, which is why it reports a reason instead of showing a toast: the
  // button explains a failure at once, the loop explains only the last one.
  //
  // `run` is the reconnect loop's cancel token. Checked between steps, and
  // before the connect in particular: a "Stop trying" that landed during the
  // 12s scan would otherwise be followed by a connect to the headband the
  // student just asked to release. The loop also keeps the panel on
  // `reconnecting` rather than stepping through scanning/connecting, so the
  // way out stays on screen for the whole attempt.
  //
  // Leaving the page cancels it too. A pairing is a chain of waits, and one
  // in flight when the page unmounts otherwise runs to completion against a
  // component that no longer exists -- sending a scan and a connect for a
  // session the unmount just ended, the same shape as the phantom session.
  const pairOnce = async (hw, activeSessionId, run = null) => {
    const cancelled = () => run?.cancelled === true || !pageAlive.current
    const phase = (p) => { if (!run) setHeadband(s => ({ ...s, phase: p })) }

    // Checked *before* the disconnect, not only after the settle: the
    // disconnect is global to the shared bridge device, so a cancelled
    // attempt must not send one.
    if (cancelled()) return { ok: false, reason: 'cancelled' }
    // Disconnect any previous session first, or the headband is left in a
    // streaming state that throws BadStateError on the next connect.
    await hw.disconnect().catch(() => {})
    await new Promise(r => setTimeout(r, 1500))
    if (cancelled()) return { ok: false, reason: 'cancelled' }

    phase('scanning')
    // session_id scopes the station reservation this scan claims, so closing
    // a different session of the same student can't release it.
    await hw.scan(activeSessionId)

    let devices = []
    for (let i = 0; i < 12; i++) {
      await new Promise(r => setTimeout(r, 1000))
      if (cancelled()) return { ok: false, reason: 'cancelled' }
      const st = await hw.status()
      devices = st?.ingestion?.muse_devices || []
      if (devices.length > 0) break
      // Stops waiting immediately if the bridge reports Bluetooth itself
      // is off, instead of burning the full 12s timeout.
      if (st?.ingestion?.bluetooth_enabled === false) return { ok: false, reason: 'bluetooth_off' }
    }
    if (devices.length === 0) return { ok: false, reason: 'no_device' }
    if (cancelled()) return { ok: false, reason: 'cancelled' }

    const target = devices[0]
    phase('connecting')
    setHeadband(s => ({ ...s, deviceName: target }))
    await hw.connect(target, activeSessionId)

    // Bridge connects asynchronously; poll for it. A BadStateError here means
    // the headband is still streaming from a prior session and needs a power-cycle.
    for (let i = 0; i < 10; i++) {
      await new Promise(r => setTimeout(r, 1000))
      if (cancelled()) return { ok: false, reason: 'cancelled' }
      const st = await hw.status()
      if (st?.ingestion?.muse_connected) {
        clearTimeout(phaseTimer.current)
        setHeadband(s => ({ ...s, connected: true, phase: 'connected', reconnect: null }))
        return { ok: true }
      }
    }
    return { ok: false, reason: 'not_connected' }
  }

  const disconnectHeadband = async (hw) => {
    clearTimeout(phaseTimer.current)
    poorStreak.current = 0
    await hw.end()
    // Drop rather than reuse: it closed over deviceId at creation, so
    // reusing it after picking a different station would misattribute data.
    setRecorder(null)
    delete window.AL_currentSessionId
    // Clear battery and contact too -- they describe the headband that just
    // left, and the battery is the one number here a student acts on.
    setHeadband(s => ({ ...s, connected: false, phase: 'idle', deviceName: null,
                         battery: null, reconnect: null, contactPoor: null }))
  }

  const toggleHeadband = async () => {
    if (!stationId) return
    if (headband.phase === 'reconnecting') {
      await cancelReconnect()
      return
    }
    // Separate from the try below so a failed session creation still resets
    // the button state and shows an alert, instead of leaving it looking
    // dead. Push pairs against a device, not a session; pull's reservation
    // is scoped by session_id, so it needs one here.
    let activeSessionId = sessionId
    try {
      if (!headband.pushMode) activeSessionId = await getOrCreateSession()
    } catch (e) {
      console.error('[headband] could not start a session', e)
      setHeadband(s => ({ ...s, phase: 'idle' }))
      toast.error('Could not start a session.', {
        description: errorDetail(e),
      })
      return
    }
    // Uses a local var, not the recorder state directly -- `setRecorder()`
    // won't take effect until the next render, so reading state later in
    // this call would still see the old value.
    let rec = recorder
    if (!rec) {
      rec = createSignalRecorder({ sessionId: activeSessionId, deviceId: stationId })
      setRecorder(rec)
      window.AL_currentSessionId = activeSessionId
    }

    const hw = makeHw(rec)

    // — Disconnect —
    if (headband.connected) {
      await disconnectHeadband(hw)
      return
    }

    clearTimeout(phaseTimer.current)
    phaseTimer.current = setTimeout(() => {
      setHeadband(s => s.phase !== 'idle' && s.phase !== 'connected' && s.phase !== 'reconnecting'
        ? { ...s, phase: 'idle', deviceName: null }
        : s)
    }, 30000)

    try {
      setHeadband(s => ({ ...s, phase: 'starting' }))
      const res = await hw.begin(activeSessionId)
      if (!res?.ok && !res?.running) throw new Error(res?.error || 'Could not start EEG session')

      const outcome = await pairOnce(hw, activeSessionId)
      if (outcome.ok) return

      setHeadband(s => ({ ...s, phase: 'idle', deviceName: null }))
      // Longer dwell than the default toast -- these are instructions the
      // student has to act on, not just read.
      if (outcome.reason === 'bluetooth_off') {
        toast.error('Bluetooth is turned off on this PC.', {
          description: 'Turn Bluetooth on in Windows Settings, then click Connect Headband again.',
          duration: 12_000,
        })
      } else if (outcome.reason === 'no_device') {
        toast.error('No headband found.', {
          description: 'Check the headband is switched on and within a metre of the computer, and that Bluetooth is enabled.',
          duration: 12_000,
        })
      } else {
        toast.error('The headband was found but would not connect.', {
          description: 'Its firmware is still streaming from a previous session. '
            + 'Hold the power button until it switches off (descending beeps), wait ten '
            + 'seconds, switch it back on, then click Connect Headband again.',
          duration: 15_000,
        })
      }

    } catch (e) {
      console.error('[headband]', e)
      clearTimeout(phaseTimer.current)
      setHeadband(s => ({ ...s, phase: 'idle', deviceName: null }))
      toast.error('The headband could not connect.', {
        description: errorDetail(e),
      })
    }
  }

  // The backend owns `user_math_performance` and derives the topic itself --
  // this page must not write to it directly, or a client update could
  // overwrite real counts.
  // Samples are stored during a session -- from the first question to Finish
  // -- and not while a headband merely sits paired before or between them.
  // Under pull that is the poller's `record` flag: Connect started it with
  // `record: false`, and this arms it. After a Finish the session is new and
  // the old poller is gone (ending a session stops it), so a recorder bound
  // to another session is replaced and a fresh poller started, recording
  // from the outset. The headband itself stays paired at the bridge
  // throughout. Push needs none of this: `startPush` is already keyed on
  // `sessionId`, so delivery there starts and stops with the session.
  const armRecording = async (activeSessionId) => {
    if (headband.pushMode || !headband.connected || !stationId) return
    let rec = recorder
    if (!rec || rec.sessionId !== activeSessionId) {
      rec = createSignalRecorder({ sessionId: activeSessionId, deviceId: stationId })
      setRecorder(rec)
      recorderRef.current = rec
      window.AL_currentSessionId = activeSessionId
    }
    const res = await rec.start({ record: true })
    if (!res?.ok) console.error('[headband] could not start recording', res?.error)
  }

  const fetchQuestion = async () => {
    setPhase('loading'); setError(false)
    try {
      const activeSessionId = await getOrCreateSession()
      // Not awaited into the question: a poller that will not arm is a
      // recording problem, not a reason to withhold a question.
      armRecording(activeSessionId).catch(e => console.error('[headband]', e))

      const params = new URLSearchParams({ user_id: user.id, bias: String(bias) })
      if (mode === 'class' && classId) params.set('class_id', classId)
      else                              params.set('grade', grade)
      params.set('session_id', activeSessionId)

      const json = await apiFetch(`/api/generate-question?${params.toString()}`)
      if (!json?.question_text) throw new Error('Invalid response')
      setData(json)
      setActiveButton(null); setSelectedAnswer(null)
      setPhase('question')
    } catch (err) {
      console.error(err); setError(true); setPhase('idle')
    }
  }

  const handleSubmit = async () => {
    const isCorrect = JSON.stringify(data.answer_options[selectedAnswer]) === JSON.stringify(data.correct_answer)
    setCorrect(isCorrect)
    setPhase('result')

    // The POST, missing-id guard, and failure toast live in `lib/session.js`,
    // shared with Practice.jsx, so both pages report failures the same way.
    // Correctness checking stays here since the two pages hold questions
    // differently.
    const res = await recordAnswer({
      sessionId: sessionIdRef.current,
      questionId: data?.id,
      selectedIndex: selectedAnswer,
      correct: isCorrect,
    })
    if (res) {
      // Counted here rather than beside `setPhase('result')`, so the figure
      // only moves for an answer that reached the database. `recordAnswer`
      // returns null on every failure path -- a missing session or question
      // id, or a failed POST -- and toasts as it goes, so an uncounted answer
      // has already been reported to the student.
      //
      // This is what `goalReached` claims: "the count only moves when an
      // answer is recorded". It was incremented optimistically before the
      // await and never rolled back, so a session whose writes were all
      // failing still announced "you have answered 10 questions" over a
      // database that held none of them.
      setSessionCount(n => n + 1)
      // Uses the topic the backend attributed the answer to -- not a local
      // guess, which is what caused these figures to disagree before.
      applyAttempt(res?.topic, isCorrect)
    }
  }

  const getAcc = (topic) => {
    const s = accuracyStats.subjects[topic]
    if (!s || s.attempts === 0) return null
    return Math.round((s.correct / s.attempts) * 100)
  }
  const totalAcc = accuracyStats.total.attempts > 0
    ? Math.round((accuracyStats.total.correct / accuracyStats.total.attempts) * 100) : null

  // `headband.samples` is the backend poller's count, which doesn't exist
  // under push -- so under push this reads from `push.recorded` instead
  // (cognitive + heart, since both come off the headband; camera has its own
  // card). Same source the RECORDING chip uses, so the two can't disagree.
  const headbandSamples = headband.pushMode
    ? ((push?.recorded?.cognitive || 0) + (push?.recorded?.heart || 0))
    : headband.samples

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
            : headband.phase === 'reconnecting'
              ? 'bg-gradient-to-br from-amber-500 to-orange-600 animate-pulse'
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
            {headband.phase === 'reconnecting' && <span className="text-[10px] font-bold px-2 py-0.5 bg-amber-100 dark:bg-amber-900/40 text-amber-700 dark:text-amber-300 rounded-full">reconnecting</span>}
            {!headband.connected && headband.phase !== 'reconnecting' && headband.available && <span className="text-[10px] font-bold px-2 py-0.5 bg-indigo-100 dark:bg-indigo-900/40 text-indigo-700 dark:text-indigo-300 rounded-full">ready</span>}
            {!headband.available && !headband.pushMode && <span className="text-[10px] font-bold px-2 py-0.5 bg-gray-100 dark:bg-gray-800 text-gray-600 dark:text-gray-300 rounded-full">offline</span>}
            {headband.pushMode && <span className="text-[10px] font-bold px-2 py-0.5 bg-gray-100 dark:bg-gray-800 text-gray-600 dark:text-gray-300 rounded-full">on your device</span>}
            {/* Three states: push === null renders nothing (not asked yet), a
                known not-recording state is amber, and only reachable +
                running shows RECORDING. */}
            {headband.pushMode && push && push.running !== true &&
             (push.reachable === false || push.enabled === false || push.running === false) && (
              <span className="text-[10px] font-bold px-2 py-0.5 bg-amber-100 dark:bg-amber-900/40 text-amber-700 dark:text-amber-300 rounded-full">not recording</span>
            )}
            {headband.pushMode && push?.reachable && push?.running && (
              <span className="text-[10px] font-bold px-2 py-0.5 bg-emerald-100 dark:bg-emerald-900/40 text-emerald-700 dark:text-emerald-300 rounded-full">● RECORDING</span>
            )}
          </p>
          <p className="text-[11px] text-gray-600 mt-0.5 dark:text-gray-400">
            {headband.phase === 'scanning'   && '🔍 Scanning for Muse headbands via Bluetooth...'}
            {headband.phase === 'connecting' && `🔗 Connecting to ${headband.deviceName || 'headband'}...`}
            {headband.phase === 'starting'   && 'Starting EEG session...'}
            {/* Attempt 0 is the bridge waiting out its first backoff, which
                is not an attempt a student should count. */}
            {headband.phase === 'reconnecting' && (
              `🔄 The headband disconnected — reconnecting${
                headband.reconnect?.attempt > 0 && headband.reconnect?.max > 0
                  ? ` (attempt ${headband.reconnect.attempt} of ${headband.reconnect.max})` : ''}…`
            )}
            {headband.phase === 'connected'  && `${headbandSamples} samples sent · teacher can see your focus & stress live`}
            {headband.phase === 'idle' && (
              headband.connected
                ? `${headbandSamples} samples sent · teacher can see your focus & stress live`
                : headband.pushMode
                  ? (push && push.enabled === false
                      ? 'The app on this computer is running but is not set up to record (PUSH_ENABLED is off). Nothing is being saved for this session.'
                    : push && push.reachable === false
                      ? 'The app on this computer is not running, so nothing is being recorded. Start it and this will change on its own.'
                      // Counts what the backend stored, not what was sent --
                      // a sent count would look healthy even for a declined
                      // sensor.
                      : push?.recorded
                        ? `${Object.values(push.recorded).reduce((a, b) => a + b, 0)} readings recorded from this computer.`
                        : 'Turn on your Muse S headband, then click Connect. It pairs through the app on this computer.')
                  : headband.available
                  ? 'EEG service ready. Turn on your Muse S headband then click Connect.'
                  : 'EEG service not reachable on port 8001. Make sure the EEGResearch backend is running.'
            )}
          </p>
          {/* The one contact reading a student can act on. Only on `=== true`:
              null is "not measured", which is most of the first seconds of a
              session and must not read as a problem. */}
          {headband.connected && headband.contactPoor === true && (
            <p className="text-[11px] font-bold text-amber-700 dark:text-amber-300 mt-1">
              ⚠ Adjust the headband so the sensors sit flat against your skin — the reading is weak.
            </p>
          )}
        </div>
        {/* Shown only when more than one headband is registered; cameras are
            already filtered out of `stations`. */}
        {stations.length > 1 && !headband.connected && (
          // aria-label needed: the placeholder option alone isn't announced
          // by some screen readers.
          <select
            aria-label="Headband"
            value={stationId || ''}
            onChange={e => setStationId(e.target.value)}
            className="text-xs font-bold rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 text-gray-700 dark:text-gray-200 px-2 py-1.5"
          >
            <option value="" disabled>Choose a headband…</option>
            {stations.map(s => (
              <option key={s.device_id} value={s.device_id}>
                {s.device_id}{s.running ? ' (in use)' : ''}
              </option>
            ))}
          </select>
        )}
        {/* Not gated on sessionId -- toggleHeadband creates one lazily.
            `available` is null under push, so gating on falsiness would
            disable the button exactly when this page is the only way to
            pair. */}
        {/* Rendered only when there's a reading -- no placeholder, since the
            gap before the first BATTERY packet is normal, not broken.
            `!= null` lets 0% through since that's a real reading. */}
        {headband.connected && headband.battery != null && (
          // aria-label alongside title -- a title tooltip isn't reliably
          // read by screen readers or shown on touch.
          <span title="Headband charge"
            aria-label={`Headband charge ${headband.battery}%`}
            className={`flex items-center gap-1 px-2.5 py-1.5 rounded-lg text-xs font-bold ${
              headband.battery <= 20 ? 'bg-rose-50 dark:bg-rose-950/40 text-rose-600 dark:text-rose-400'
              : headband.battery <= 40 ? 'bg-amber-50 dark:bg-amber-950/40 text-amber-600 dark:text-amber-400'
              : 'bg-gray-100 dark:bg-gray-800 text-gray-600 dark:text-gray-300'}`}>
            {headband.battery <= 20 ? <BatteryLow size={14} /> : <BatteryFull size={14} />}
            {Math.round(headband.battery)}%
          </span>
        )}
        <button onClick={toggleHeadband}
          disabled={(!headband.available && !headband.pushMode) || !stationId || ['starting','scanning','connecting'].includes(headband.phase)}
          className={`px-4 py-2 rounded-xl text-sm font-bold transition shadow disabled:opacity-50 disabled:cursor-not-allowed ${
            headband.connected || headband.phase === 'reconnecting'
              ? 'bg-rose-500 hover:bg-rose-600 text-white' : 'bg-indigo-600 hover:bg-indigo-700 text-white'
          }`}>
          { headband.phase === 'starting'     ? 'Starting...'
          : headband.phase === 'scanning'     ? 'Scanning...'
          : headband.phase === 'connecting'   ? 'Connecting...'
          : headband.phase === 'reconnecting' ? 'Stop trying'
          : headband.connected                ? 'Disconnect'
          :                                   'Connect Headband' }
        </button>
      </motion.div>

      {/* Shown only when a camera is registered. Disabled, not hidden, under
          pull -- `face_signals` has only one writer (the push endpoint), so
          a camera under pull would capture nothing storable. */}
      {camera.id && (
        <motion.div initial={{ opacity: 0, y: -6 }} animate={{ opacity: 1, y: 0 }}
          className="mb-6 rounded-2xl bg-white dark:bg-gray-900 border border-gray-100 dark:border-gray-800 p-4 flex items-center gap-4 shadow-sm">
          <div className="w-11 h-11 rounded-xl bg-fuchsia-600 grid place-items-center text-white text-lg">📷</div>
          <div className="flex-1 min-w-0">
            {/* Camera being on and a lesson recording are different claims --
                only camera.running && sessionId earns the RECORDING dot. */}
            <p className="font-bold text-sm flex items-center gap-2">
              Camera
              {camera.running && sessionId
                ? <span className="text-[10px] font-bold px-2 py-0.5 bg-emerald-100 dark:bg-emerald-900/40 text-emerald-700 dark:text-emerald-300 rounded-full">● RECORDING</span>
                : camera.running
                  ? <span className="text-[10px] font-bold px-2 py-0.5 bg-amber-100 dark:bg-amber-900/40 text-amber-700 dark:text-amber-300 rounded-full">on, not recording</span>
                  : <span className="text-[10px] font-bold px-2 py-0.5 bg-gray-100 dark:bg-gray-800 text-gray-600 dark:text-gray-300 rounded-full">off</span>}
            </p>
            <p className="text-[11px] text-gray-600 mt-0.5 dark:text-gray-400">
              {!headband.pushMode
                ? 'The camera records through the app on this computer, which this deployment is not set up for. Nothing would be saved.'
                : camera.busy
                  ? 'Starting the camera...'
                  : camera.running && sessionId
                    ? 'Reading how you are finding the questions. No video is saved.'
                    : camera.running
                      ? 'The camera is on, but nothing is being recorded until you start a lesson. No video is saved.'
                      : 'Turn on to read how you are finding the questions. No video is saved.'}
            </p>
          </div>
          <button onClick={toggleCamera}
            disabled={!headband.pushMode || camera.busy}
            className={`px-4 py-2 rounded-xl text-sm font-bold transition shadow disabled:opacity-50 disabled:cursor-not-allowed ${
              camera.running ? 'bg-rose-500 hover:bg-rose-600 text-white' : 'bg-fuchsia-600 hover:bg-fuchsia-700 text-white'
            }`}>
            {camera.busy ? 'Working...' : camera.running ? 'Turn off' : 'Turn on camera'}
          </button>
        </motion.div>
      )}

      {/* Asked, not enforced -- a banner, not a modal, so it never blocks an
          in-progress question. Dismissing clears only this reminder, not the
          preference. */}
      {timeUp && (
        <motion.div initial={{ opacity: 0, y: -6 }} animate={{ opacity: 1, y: 0 }}
          className="mb-6 bg-amber-50 dark:bg-amber-950/40 border border-amber-200 dark:border-amber-800 rounded-2xl px-5 py-4 flex flex-wrap items-center gap-3">
          <Clock size={18} className="text-amber-600 dark:text-amber-400" />
          <p className="text-sm font-bold text-amber-800 dark:text-amber-200 flex-1 min-w-[14rem]">
            That is your {durationMin} minutes. Nice work — finish up, or carry on if you are enjoying it.
          </p>
          <button onClick={() => setTimeUpDismissed(true)}
            className="px-4 py-2 rounded-xl text-sm font-bold border border-amber-300 dark:border-amber-700 text-amber-800 dark:text-amber-200 hover:bg-amber-100 dark:hover:bg-amber-900/40 transition">
            Keep going
          </button>
          <button onClick={finishSession} disabled={finishing}
            className="px-4 py-2 rounded-xl text-sm font-bold bg-amber-600 hover:bg-amber-700 text-white shadow transition disabled:opacity-60">
            {finishing ? 'Finishing…' : 'Finish session'}
          </button>
        </motion.div>
      )}

      {/* The same shape as the duration reminder above, and for the same
          reason: a banner rather than a modal, so it never blocks a question
          in progress, and "Keep going" clears the reminder without clearing
          the goal. */}
      {goalReached && (
        <motion.div initial={{ opacity: 0, y: -6 }} animate={{ opacity: 1, y: 0 }}
          className="mb-6 bg-emerald-50 dark:bg-emerald-950/40 border border-emerald-200 dark:border-emerald-800 rounded-2xl px-5 py-4 flex flex-wrap items-center gap-3">
          <Sparkles size={18} className="text-emerald-600 dark:text-emerald-400" />
          <p className="text-sm font-bold text-emerald-800 dark:text-emerald-200 flex-1 min-w-[14rem]">
            That is {questionGoal} questions answered. Nice work — finish up, or carry on if you are enjoying it.
          </p>
          <button onClick={() => setGoalDismissed(true)}
            className="px-4 py-2 rounded-xl text-sm font-bold border border-emerald-300 dark:border-emerald-700 text-emerald-800 dark:text-emerald-200 hover:bg-emerald-100 dark:hover:bg-emerald-900/40 transition">
            Keep going
          </button>
          <button onClick={finishSession} disabled={finishing}
            className="px-4 py-2 rounded-xl text-sm font-bold bg-emerald-600 hover:bg-emerald-700 text-white shadow transition disabled:opacity-60">
            {finishing ? 'Finishing…' : 'Finish session'}
          </button>
        </motion.div>
      )}

      <div className="grid lg:grid-cols-3 gap-6">
        <div className="lg:col-span-2 space-y-4">
          {sessionCount > 0 && (
            <div className="flex gap-3 flex-wrap items-center">
              {/* Under pull the sidecar reports no counts (the poller writes
                  instead), so headband.connected is used there. */}
              <RecordingIndicator channels={
                headband.pushMode ? recording
                  : headband.connected ? ['Headband'] : []
              } />
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
                  The AI analyses your performance across {TOPICS.length} topics and picks the one you need most.
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
                  <p className="text-[11px] text-gray-600 mt-2 text-center dark:text-gray-400">
                    Generating <strong>{biasLabel}</strong> questions for <strong>{effectiveGrade}</strong>
                  </p>
                </div>

                {/* How many questions this sitting. A goal, not a cap: the
                    banner it raises can be dismissed, for the same reason the
                    duration one can -- ending a session on a threshold would
                    throw away a question the student is part way through. */}
                <div className="max-w-md mx-auto mb-6">
                  <label className="block text-sm font-bold text-gray-700 dark:text-gray-300 mb-2 text-center">How many questions?</label>
                  <div className="flex items-center justify-center gap-2 flex-wrap">
                    {[5, 10, 15, 20, null].map(n => (
                      <button key={n ?? 'none'} onClick={() => { setQuestionGoal(n); setGoalDismissed(false) }}
                        className={`px-4 py-2 rounded-xl text-sm font-bold transition border ${questionGoal === n ? 'bg-indigo-600 text-white border-indigo-600 shadow' : 'border-gray-200 dark:border-gray-700 text-gray-600 dark:text-gray-300 hover:border-indigo-400'}`}>
                        {n ?? 'No limit'}
                      </button>
                    ))}
                  </div>
                  <p className="text-[11px] text-gray-600 mt-2 text-center dark:text-gray-400">
                    {questionGoal
                      ? <>We will check in after <strong>{questionGoal}</strong> — you can always keep going.</>
                      : <>Practise for as long as you like.</>}
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
                <QuestionFigure figure={data.figure} />

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
                      <span className="text-xs text-gray-600 dark:text-gray-400">Next question:</span>
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
                    <span className={`text-xs font-black ${acc === null ? 'text-gray-600' : acc >= 70 ? 'text-green-600 dark:text-green-400' : acc >= 40 ? 'text-amber-600 dark:text-amber-400' : 'text-rose-600 dark:text-rose-400'}`}>
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
                  {s?.attempts > 0 && <p className="text-[10px] text-gray-600 mt-0.5 dark:text-gray-400">{s.correct}/{s.attempts} correct</p>}
                </div>
              )
            })}
          </div>

          {/* No reset button here -- deleting `user_math_performance` would
              be a one-click, unconfirmed erasure of academic data the
              adaptive engine relies on. Erasure is a parent-only, confirmed
              action elsewhere. */}
          {accuracyState === 'failed' && (
            <p className="mt-5 text-xs text-amber-500">
              Could not load your topic history just now — these are not your real figures.
            </p>
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
              {/* Fires only when the sidecar itself didn't answer
                  (`=== false`), not on mode alone -- the backend's own push
                  payload reports `available: null`, which would misread as
                  an outage if treated as falsy. */}
              {eegDebug?.ingest_mode === 'push' && eegDebug?.available === false ? (
                <p className="text-yellow-300">INGEST_MODE=push — the sidecar on this device is not answering, so there is nothing to show. Start it and this panel fills in on its own.</p>
              ) : !eegDebug || !eegDebug.available ? (
                <p className="text-red-400">⚠ EEGResearch not reachable on port 8001. Start it with: <span className="text-yellow-300">uvicorn src.app.main:app --port 8001</span></p>
              ) : (() => {
                const snap    = eegDebug.snapshot
                const muse    = eegDebug.muse    || {}
                const state   = snap?.state      || {}
                const feat    = snap?.features   || {}
                const bands   = snap?.bands      || {}
                const ing     = muse?.ingestion  || snap?.ingestion || {}
                const museSvcRunning = muse?.running
                // Scores are meaningless with bad electrode contact -- libMuse
                // still computes confident-looking numbers from garbage data.
                // Blanked only when contact is known bad or there's no data;
                // the legacy "poor" heuristic (no contact info) reports poor
                // for any focused student, so blanking on that would hide
                // every score.
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

                const stateColor = { focused: 'text-green-400', stressed: 'text-red-400', neutral: 'text-yellow-400', insufficient_signal: 'text-gray-400' }

                return (
                  <>
                    {/* Row 0 — pipeline status */}
                    <div className="border border-gray-700 rounded p-2 space-y-1">
                      <p className="text-gray-400 text-[10px] uppercase tracking-widest mb-1">Pipeline</p>
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
                        <span className={snap ? 'text-green-400' : 'text-gray-400'}>
                          {snap ? '✓' : '○'} EEG samples flowing
                        </span>
                      </div>
                    </div>

                    {/* Row 1 — connection */}
                    <div className="flex flex-wrap gap-4">
                      <div>
                        <p className="text-gray-400 mb-1">EEG Source</p>
                        <p className="text-white">{ing.eeg_source || '—'}</p>
                      </div>
                      <div>
                        <p className="text-gray-400 mb-1">Bridge Mode</p>
                        <p className="text-white">{ing.bridge_mode || '—'}</p>
                      </div>
                      <div>
                        <p className="text-gray-400 mb-1">Signal Quality</p>
                        <p className={{ good: 'text-green-400', degraded: 'text-yellow-400', poor: 'text-red-400' }[feat.signal_quality] || 'text-gray-400'}>
                          {feat.signal_quality || (snap ? 'no data' : museSvcRunning ? 'waiting for bridge' : 'no session')}
                        </p>
                      </div>
                      <div>
                        <p className="text-gray-400 mb-1">Learner State</p>
                        <p className={stateColor[state.label] || 'text-gray-400'}>{state.label || '—'}</p>
                      </div>
                      {/* No difficulty tile here -- difficulty is chosen by
                          the backend from correctness, topic history, and
                          grade, not by the headband. Showing one next to live
                          EEG would imply otherwise. */}
                    </div>

                    {/* Row 2 — scores */}
                    <div className="grid grid-cols-2 gap-3">
                      <div>
                        <p className="text-gray-400 mb-1">Focus <span className="text-white">{untrusted ? '—' : pct(feat.focus_score)}</span></p>
                        {bar(untrusted ? null : feat.focus_score, 'bg-blue-500')}
                      </div>
                      <div>
                        <p className="text-gray-400 mb-1">Calm <span className="text-white">{untrusted ? '—' : pct(feat.calm_score)}</span></p>
                        {bar(untrusted ? null : feat.calm_score, 'bg-emerald-500')}
                      </div>
                      <div>
                        <p className="text-gray-400 mb-1">Confidence <span className="text-white">{untrusted ? '—' : pct(feat.confidence)}</span></p>
                        {bar(untrusted ? null : feat.confidence, 'bg-violet-500')}
                      </div>
                      <div>
                        <p className="text-gray-400 mb-1">Stress (derived) <span className="text-white">{feat.calm_score != null && !untrusted ? pct(1 - (feat.calm_score > 1 ? feat.calm_score / 100 : feat.calm_score)) : '—'}</span></p>
                        {bar(feat.calm_score != null && !untrusted ? 1 - (feat.calm_score > 1 ? feat.calm_score / 100 : feat.calm_score) : null, 'bg-red-500')}
                      </div>
                    </div>

                    {/* Row 3 — bands */}
                    {Object.keys(bands).length > 0 && (
                      <div>
                        <p className="text-gray-400 mb-1">EEG Bands</p>
                        <div className="flex flex-wrap gap-x-4 gap-y-1">
                          {['delta','theta','alpha','beta','gamma'].map(b => (
                            <span key={b} className="text-white">{b}: <span className="text-yellow-300">{bands[b] != null ? bands[b].toFixed(3) : '—'}</span></span>
                          ))}
                        </div>
                      </div>
                    )}

                    {/* Row 4 — raw state reason */}
                    {state.reason && (
                      <p className="text-gray-400">Reason: <span className="text-white">{state.reason}</span></p>
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