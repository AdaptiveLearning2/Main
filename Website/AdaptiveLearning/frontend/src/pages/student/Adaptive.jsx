import { useState, useEffect, useEffectEvent, useRef, useCallback } from 'react'
import { m } from 'framer-motion'
import { useAuth } from '../../context/AuthContext'
import { supabase } from '../../lib/supabase'
import { apiFetch, apiFetchOnUnload } from '../../lib/api'
import { endSession, recordAnswer } from '../../lib/session'
import useEegStartReport from '../../hooks/useEegStartReport'
import usePoll from '../../hooks/usePoll'
import useValueChange from '../../hooks/useValueChange'
import { PUSH_POLL_MS, PULL_HEALTH_POLL_MS, PULL_STATUS_POLL_MS, PUSH_STATUS_POLL_MS } from './pollIntervals'
import { onSignOut } from '../../lib/signOutTasks'
import { createSignalRecorder, eegHealth, eegStatus, eegDevices } from '../../lib/signals'
import { reloadIfRestored } from '../../lib/pageRestore'
import { onPermitsChanged } from '../../lib/permitsChanged'
import { startPush, stopPush, stopPushOnUnload, pushStatus,
         deviceStart, deviceStop, deviceStopOnUnload, museRefresh, museConnect,
         museDisconnect, museState, devices as sidecarDevices,
         releasePushIfIdle, sidecarDebug
       } from '../../lib/sidecar'
import RecordingIndicator from '../../components/signals/RecordingIndicator'
import { GraduationCap, User, Minus, Plus, Sparkles, Brain, BatteryFull, BatteryLow, Clock } from 'lucide-react'
import { toast } from 'sonner'
import QuestionFigure from '../../components/questions/QuestionFigure'
import CCSSBadge from '../../components/questions/CCSSBadge'
import { TOPICS as ALL_TOPICS, TOPIC_ICONS, topicLabel, topicsToShow } from '../../lib/topics'
import useGradeTopics from '../../hooks/useGradeTopics'
import { contactQuality } from '../../lib/contactQuality'
import { GRADES } from '../../lib/grades'

const EEG_DEBUG = import.meta.env.VITE_EEG_DEBUG === 'true'
// Device-list retry while empty (the sidecar often starts later); matches the health check.
const DISCOVERY_RETRY_MS = 5000

// Page-driven headband recovery, only once the bridge has given up (or is too old to try).
const RECONNECT_ATTEMPTS = 3
const RECONNECT_BACKOFF_MS = [2000, 4000, 8000]
// Faster status poll while recovering a drop.
const RECONNECT_POLL_MS = 2000
const TELEMETRY_POLL_MS = 5000
// Consecutive poor contact frames before the hint shows; one frame is noise.
const CONTACT_POOR_STREAK = 2
// Minimum gap between disconnect toasts, for a flapping link.
const DROP_TOAST_MIN_MS = 60_000
// Max EEG packet age for Connect to adopt an existing link; under the bridge's 8s watchdog.
const ADOPT_MAX_EEG_AGE_MS = 3000

// The one "is this link alive" rule: libMuse says CONNECTED after EEG stops,
// so a fresh packet is required. An older bridge reports no age: never alive.
const linkAlive = (ing) =>
  ing?.muse_connected === true
  && typeof ing.eeg_age_ms === 'number' && ing.eeg_age_ms <= ADOPT_MAX_EEG_AGE_MS

// Connected with no packet yet (a new link or preset switch): not alive, not
// dead. Recovery grants it SETTLE_GRACE_MS before treating it as dead.
const linkSettling = (ing) =>
  ing?.muse_connected === true && ing.eeg_age_ms == null
// Above the 5s preset settle and the bridge's 8s watchdog, so the bridge decides first.
const SETTLE_GRACE_MS = 10_000

// Retry delay for offering the session to a sidecar not yet started.
const PUSH_RETRY_MS = 5000
// Waits before re-reading a profile that failed to load; after the last the grade stays unknown.
const PROFILE_RETRY_MS = [1000, 4000, 15000]

// Labeled by the sensor a student recognizes, not by table name.
const CHANNEL_LABELS = [
  ['cognitive', 'Headband'],
  ['heart',     'Heart sensor'],
  ['face',      'Camera'],
]

// `GET /api/recording/me` (push only). The verdicts are the backend's `sensors`; the states only pick words.
const PERMIT_TIMEOUT_MS = 8000
// A sidecar answer stands in for the page's own while observed within this many of the sidecar's own check
// intervals (its push status says how long one is); a missed check or two, not a sidecar gone quiet.
const SIDECAR_FRESH_CHECKS = 1.5
const PERMIT_REASONS = {
  // Names no one: the student may have withdrawn it themselves.
  declined: "it isn't permitted (see Sensors on your Profile)",
  switched_off: "switched off by the school's administrator",
  school_year_not_started: 'nothing is recorded outside the school year',
  school_year_ended: 'nothing is recorded outside the school year',
  school_year_unconfigured: 'no school year has been set up yet',
  unknown: "couldn't check, this retries on its own",
}
const permitReason = (state) => PERMIT_REASONS[state] || "recording isn't permitted right now"

/**
 * What an answer lets the page switch on. `answer` is undefined before the first one lands and null for a
 * failed read; then, as for an answer without `sensors`, nothing is allowed and nothing refused.
 */
const permitVerdicts = (answer) => {
  const state = (c) => answer?.[c]?.state ?? 'unknown'
  const sensor = (name, states) => ({
    checked: answer !== undefined, states,
    allowed: answer?.sensors?.[name]?.allowed === true,
    refused: answer?.sensors?.[name]?.refused === true,
  })
  return {
    camera: sensor('camera', [['camera', state('camera')]]),
    headband: sensor('headband', [['EEG', state('eeg')], ['heart rate', state('headband_optical')]]),
  }
}

/** Why a sensor may not be switched on, as a sentence. `sensor` is 'camera' or 'headband'. */
const blockedLine = (sensor, verdict) => {
  if (!verdict.checked) return `Checking whether the ${sensor} may record…`
  if (verdict.states.every(([, s]) => s === 'unknown')) {
    return `Couldn't check whether the ${sensor} may record; this retries on its own.`
  }
  const reasons = verdict.states.map(([, s]) => permitReason(s))
  const named = new Set(reasons).size === 1
    ? reasons[0]
    : verdict.states.map(([label, s]) => `${label} — ${permitReason(s)}`).join('; ')
  return `${sensor === 'camera' ? 'Camera' : 'Headband'} recording is off: ${named}.`
}

const TOPICS = ALL_TOPICS
const ICONS  = TOPIC_ICONS
const SHORT  = { angle_relationships: 'Angle Rel.' }

/** An error as a toast description. */
const errorDetail = (e) => e?.message || String(e)

const initSubjects = () => {
  const s = {}; TOPICS.forEach(t => { s[t] = { correct: 0, attempts: 0 } }); return s
}


/**
 * `headband.pushMode` mirrors the backend's `ingest_mode`. Pull: the backend
 * polls the sidecar; hardware goes through `/api/eeg/*`. Push: this page drives
 * the local sidecar via `lib/sidecar.js`, and `/api/eeg/*` answers 409.
 */
export default function Adaptive() {
  const { user } = useAuth()

  // mode: 'solo' (pick your own grade) | 'class' (use class grade)
  const [mode, setMode] = useState(() => localStorage.getItem('adaptive_mode') || 'solo')
  // '' is no grade: nothing names one, so the backend serves its default.
  const [grade, setGrade] = useState('')
  // The profile's grade, which class mode is served when the class has none (`_served_grade`).
  const [savedGrade, setSavedGrade] = useState('')
  // null while reading, false if the read failed: then "no saved grade" is unknown, not a fact.
  const [profileRead, setProfileRead] = useState(null)
  const [profileAttempt, setProfileAttempt] = useState(0)
  const [classes, setClasses] = useState([])
  const [classId, setClassId] = useState('')
  const [bias, setBias] = useState(0) // -1 easier, 0 auto, +1 harder

  // Planned length from the profile; advisory, asked between questions. Null until loaded.
  const [durationMin, setDurationMin]         = useState(null)
  // Questions wanted this sitting, or null for no limit. Per session, not saved.
  const [questionGoal, setQuestionGoal]       = useState(null)
  const [goalDismissed, setGoalDismissed]     = useState(false)
  const [sessionStartedAt, setSessionStartedAt] = useState(null)
  const [elapsedMin, setElapsedMin]           = useState(0)
  const [timeUpDismissed, setTimeUpDismissed] = useState(false)
  const [finishing, setFinishing]             = useState(false)

  // Topic accuracy from the server's `user_math_performance`, never a browser cache.
  // `accuracyState` separates a failed read from no history.
  const [accuracyStats, setAccuracyStats] = useState(
    () => ({ total: { correct: 0, attempts: 0 }, subjects: initSubjects() }))
  const [accuracyState, setAccuracyState] = useState('loading')  // loading | ready | failed

  // Keyed on the user id, not the object, so a recreated `user` doesn't re-fetch.
  const uid = user?.id
  // A chain: state set after an `await` counts as set synchronously in the effect.
  const loadAccuracy = useCallback(() => {
    if (!uid) return
    // Same endpoint as StudentProgressReport: one reader, one access check.
    return apiFetch(`/api/performance/student/${uid}`).then(rows => {
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
    }, e => {
      console.error('[accuracy] could not load topic performance', e)
      setAccuracyState('failed')
    })
  }, [uid])

  // Mirrors the +1 the backend made, by the topic name it sent, instead of re-fetching.
  const applyAttempt = useCallback((topic, wasCorrect) => {
    if (!topic) return                 // nothing was attributed; nothing moved
    const bump = ({ correct, attempts }) => ({
      correct:  correct  + (wasCorrect ? 1 : 0),
      attempts: attempts + 1,
    })
    setAccuracyStats(prev => {
      const prior = prev.subjects[topic]
      // An unrecognized topic (no tile) still counts toward the total.
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
    // `pushMode` unset until a health check lands. `available` null until a probe
    // answers ("not checked" is not "down"); gates read null as falsy.
    available: null, connected: false, samples: 0, lastTs: null,
    // Pull only: the poller runs but an admin switch has it writing nothing.
    withheld: false,
    // `reconnecting`: a dropped link being recovered; `connected` stays false throughout.
    phase: 'idle', // idle | starting | scanning | connecting | connected | reconnecting
    deviceName: null,
    // Charge percent, or null for no reading; never rendered as 0%.
    battery: null,
    // {attempt, max, byBridge} while reconnecting, else null; `attempt` 0 during the bridge's first backoff.
    reconnect: null,
    // Debounced electrode contact; null until measured.
    contactPoor: null,
  })

  // Registered sidecar EEG devices, chosen before the BLE scan (not muse_devices).
  const [stations, setStations]     = useState([])
  // Separate from `stations` (headbands only), so the picker never offers a camera.
  const [camera, setCamera]         = useState(
    { id: null, running: false, busy: false })
  const [stationId, setStationId]   = useState(null)

  // The sidecar's own delivery report under push; null until asked.
  const [push, setPush]               = useState(null)
  // The session the sidecar delivers for, per push status: undefined before any answer, null when none.
  const [sidecarSession, setSidecarSession] = useState(undefined)
  // This tab's lessons: a status still naming one it ended is that lesson winding down, not another tab's.
  const ownSessions = useRef(new Set())
  // Bumped on every take-over, so a status reply sent before one cannot undo it.
  const ownershipEpoch = useRef(0)
  // Channels that delivered since the last poll, not merely consented ones.
  const [recording, setRecording]     = useState([])
  // Last poll's cumulative counts, for a delta.
  const lastRecorded = useRef(null)
  // Last poll's per-channel results, to see one turn to declined.
  const lastResults = useRef({})
  // Chains start and stop so a teardown can't race an in-flight start.
  const pushHandoff = useRef(Promise.resolve())
  // Set beside every `setSessionId`, never copied from it by an effect: a late copy wrote a stale id
  // back over a newer one. Handlers and the stable `recover` callback read this, not the state.
  const sessionIdRef = useRef(null)
  // Answers sent and not yet settled; while any is, `pagehide` leaves the session open.
  const answersInFlight = useRef(0)

  // The latest applied `/api/recording/me` answer (see `permitVerdicts`); the ref is for loops between renders.
  const [permits, setPermits] = useState({ answer: undefined })
  const permitsRef = useRef(undefined)
  // When the applied answer was observed, and when the sidecar last observed one the page took; on
  // `performance.now()`, which a clock set back cannot rewind. And how long the sidecar's answer is trusted.
  const permitsAt = useRef(-Infinity)
  const sidecarAt = useRef(-Infinity)
  const sidecarTrustMs = useRef(PUSH_POLL_MS * SIDECAR_FRESH_CHECKS)
  // Devices with a stop under way, kept until an effect sees them off: an effect from a render before
  // the stop's state update still sees them on, and must not stop them again (see `enforcePermits`).
  const stoppingIds = useRef(new Set())
  // Whether a sensor's "could not be switched off" was already said, until the sensor is seen off.
  const stopFailed = useRef({ camera: false, headband: false })
  // State mirrors for timer-driven polls, the reconnect loop, `pagehide` and `applyPermits`, which run
  // between renders; declared before their first reader.
  const headbandRef = useRef(headband)
  const cameraRef = useRef({ id: null, running: false, pushMode: undefined })
  const stationsRef = useRef([])
  useEffect(() => { stationsRef.current = stations }, [stations])
  // By when it was observed, not when it arrived: an older answer landing late changes nothing.
  const applyPermits = (at, answer) => {
    if (at <= permitsAt.current) return
    permitsAt.current = at
    const same = JSON.stringify(answer) === JSON.stringify(permitsRef.current)
    permitsRef.current = answer
    // Unchanged, no re-render, unless it refuses a sensor that is on: the next answer is what retries a
    // stop that did not happen, a failed one or one a failed Turn off stood in for.
    const v = permitVerdicts(answer)
    const h = headbandRef.current
    const refusedAndOn = (v.camera.refused && cameraRef.current.running)
      || (v.headband.refused && (h.connected || h.phase !== 'idle' || stationsRef.current.some(s => s.running)))
    if (same && !refusedAndOn) return
    setPermits({ answer })
  }
  // Resolves to this request's own answer: a click decides on what it just asked, whatever else landed.
  const checkPermits = () => {
    const at = performance.now()
    return apiFetch('/api/recording/me', { timeoutMs: PERMIT_TIMEOUT_MS })
      .catch(() => null)
      .then(answer => {
        applyPermits(at, answer)
        return answer
      })
  }
  // The sidecar's answer, from its push status: observed `ageSeconds` ago, by the same backend.
  const takeSidecarPermits = useEffectEvent((answer, ageSeconds, checkSeconds) => {
    const at = performance.now() - ageSeconds * 1000
    sidecarAt.current = Math.max(sidecarAt.current, at)
    if (typeof checkSeconds === 'number') sidecarTrustMs.current = checkSeconds * 1000 * SIDECAR_FRESH_CHECKS
    applyPermits(at, answer)
  })
  const headbandRefused = () => permitVerdicts(permitsRef.current).headband.refused

  // Dev-only EEG debug panel
  const [eegDebug, setEegDebug]       = useState(null)
  const [debugOpen, setDebugOpen]     = useState(true)
  const debugTimer  = useRef(null)
  const phaseTimer  = useRef(null)
  // The status poll's latest landed answer, which the telemetry poll reuses under pull.
  const lastStatus = useRef(null)
  const recorderRef = useRef(null)
  // Last drop toast time, and whether the current drop was announced (see onDropped).
  const lastDropToast = useRef(0)
  const dropAnnounced = useRef(false)
  // When a reconnecting link was first seen settling (see linkSettling).
  const settlingSince = useRef(null)

  // Whose lesson the one sidecar delivers for (push): this tab's, another tab's or window's (also for a tab with no
  // lesson yet), or not known. The newest lesson takes it; a page whose lesson it isn't stops or takes back nothing.
  const pushOwner = !sidecarSession ? 'unknown' : sidecarSession === sessionId ? 'mine' : 'elsewhere'
  const pushOwnerRef = useRef(pushOwner)

  // The camera stops when this page goes away (the headband stays paired): route
  // change via cleanup, tab close via `pagehide`, both reading a synced ref.
  useEffect(() => {
    cameraRef.current = { id: camera.id, running: camera.running, pushMode: headband.pushMode }
    pushOwnerRef.current = pushOwner
  })
  useEffect(() => {
    // Push only, like `toggleCamera`: under pull the backend owns the device. Not another tab's lesson's camera.
    const stoppable = () => {
      const c = cameraRef.current
      return c.running && c.id && c.pushMode && pushOwnerRef.current !== 'elsewhere' ? c.id : null
    }
    const onPageHide = (e) => {
      const id = stoppable()
      if (id) { deviceStopOnUnload(id); reloadIfRestored(e) }
    }
    window.addEventListener('pagehide', onPageHide)
    return () => {
      window.removeEventListener('pagehide', onPageHide)
      const id = stoppable()
      if (id) deviceStop(id).catch(() => {})
    }
  }, [])

  // The running page-driven reconnect's token, or null; an object so a cancel
  // reaches the loop actually running.
  const reconnectRun = useRef(null)
  // False once the page is gone; `pairOnce` reads it between steps. Set true in
  // the effect so StrictMode's dev remount doesn't leave it false.
  const pageAlive = useRef(true)
  useEffect(() => {
    pageAlive.current = true
    return () => {
      pageAlive.current = false
      // Cancel the reconnect loop too, or it keeps disconnecting the shared bridge.
      if (reconnectRun.current) reconnectRun.current.cancelled = true
      reconnectRun.current = null
    }
  }, [])
  // Consecutive poor contact readings; the hint needs CONTACT_POOR_STREAK.
  const poorStreak = useRef(0)

  useEffect(() => { headbandRef.current = headband }, [headband])
  useEffect(() => { recorderRef.current = recorder }, [recorder])

  // Leaving the page ends the session: an empty one is discarded server-side,
  // one with answers is closed.
  useEffect(() => () => {
    if (sessionIdRef.current) endSession(sessionIdRef.current)
  }, [])

  // A reload or a tab close skips the unmount above, and `start_session` cannot tell this tab's lesson from
  // another tab's live one, so the page ends its own on the way out. A duplicated tab fires nothing here.
  useEffect(() => {
    const onHide = (e) => {
      const id = sessionIdRef.current
      // An answer still in flight could land after the end and be refused: leave it open, for a later
      // start or the sweep, rather than lose the answer.
      if (!id || answersInFlight.current > 0) return
      apiFetchOnUnload(`/api/sessions/${id}/end`, {})
      sessionIdRef.current = null
      // Restored from the back-forward cache, the page would answer into the session it just ended.
      reloadIfRestored(e)
    }
    window.addEventListener('pagehide', onHide)
    return () => window.removeEventListener('pagehide', onHide)
  }, [])

  // Sign-out clears the token before unmount, so the cleanup above would 401.
  // The last attempt: cleared first so a failure is not retried tokenless, and state too so
  // a page left up by a failed sign-out starts a new session rather than answering into this one.
  useEffect(() => onSignOut(async () => {
    const id = sessionIdRef.current
    sessionIdRef.current = null
    setSessionId(null)
    if (id) await endSession(id)
  }), [])

  // Unmount: stop the 30s connect safety timer and drop the global session id.
  useEffect(() => () => {
    clearTimeout(phaseTimer.current)
    delete window.AL_currentSessionId
  }, [])

  // Per-session clock state, cleared with the session. The clock starts in
  // `fetchQuestion`, not here, so headband setup isn't charged to the session.
  useValueChange(sessionId, id => {
    if (id) return
    setSessionStartedAt(null)
    setElapsedMin(0)
    setTimeUpDismissed(false)
    // Here, since every path to "no session" changes `sessionId`.
    // `questionGoal` is kept: it is the sitting's choice.
    setGoalDismissed(false)
  })

  useEffect(() => {
    if (!sessionStartedAt || !durationMin) return
    // Every 20s: precise enough for a reminder.
    const tick = () => setElapsedMin((Date.now() - sessionStartedAt) / 60000)
    tick()
    const id = setInterval(tick, 20000)
    return () => clearInterval(id)
  }, [sessionStartedAt, durationMin])

  // A question goal silences the duration reminder; "No limit" (null) keeps it.
  const timeUp = !!durationMin && !questionGoal && !timeUpDismissed && elapsedMin >= durationMin

  // Counts only recorded answers, so it never fires mid-question. Asked, not enforced.
  const goalReached = !!questionGoal && !goalDismissed && sessionCount >= questionGoal

  // load profile default grade; a failed read is retried, or the grade stays "unknown" all visit
  useEffect(() => {
    let cancelled = false
    let retry = null
    apiFetch('/api/profile/me').then(p => {
      if (cancelled) return
      // A retry landing late must not undo a grade the student has picked since.
      if (p?.grade_level) { setGrade(g => g || p.grade_level); setSavedGrade(p.grade_level) }
      // `!= null`: 0 (Auto) is a valid bias.
      if (p?.difficulty_bias != null) setBias(p.difficulty_bias)
      if (p?.session_duration_minutes != null) setDurationMin(p.session_duration_minutes)
      setProfileRead(true)
    }).catch(e => {
      if (cancelled) return
      // 404: no profile row, which is a known "no saved grade", not an unknown one.
      if (e?.status === 404) { setProfileRead(true); return }
      setProfileRead(false)
      if (profileAttempt < PROFILE_RETRY_MS.length) {
        retry = setTimeout(() => setProfileAttempt(a => a + 1), PROFILE_RETRY_MS[profileAttempt])
      }
    })
    return () => { cancelled = true; clearTimeout(retry) }
  }, [profileAttempt])

  // load classes
  useEffect(() => {
    apiFetch('/api/classes', { cache: true }).then(c => {
      setClasses(c || [])
      // Functional, so a class already chosen is kept.
      if ((c || []).length) setClassId(prev => prev || c[0].id)
    }).catch(()=>{})
  }, [])

  // Check EEG health independently so the button isn't stuck unavailable if session start fails.
  // Under push its answer is fixed by configuration, so it is re-checked every 30 s, not 5.
  usePoll((stopped) => eegHealth()
    .then(h => {
      // A refused probe says nothing about the sidecar; keep the last answer.
      if (!stopped() && h.refused) return setHeadband(s => ({ ...s, probeRefused: true }))
      // Runs before a session exists, so pushMode is known before first paint.
      if (!stopped()) setHeadband(s => ({
        ...s,
        // Only a probe that landed (`answered`, not a missing `ingest_mode`)
        // may set the mode, clear `serviceError`, or clear `probeRefused`.
        ...(h.answered === false ? {} : {
          pushMode: h.ingest_mode === 'push',
          serviceError: h.error || null,
          probeRefused: false,
        }),
        // Written either way: an unreached probe disables Connect, and
        // `probeUnreachable` says it was our backend, not the sidecar.
        available: !!h.available,
        probeUnreachable: h.answered === false,
      }))
    })
    .catch(() => { if (!stopped()) setHeadband(s => ({ ...s, available: false })) }),
  { intervalMs: headband.pushMode ? PUSH_POLL_MS : PULL_HEALTH_POLL_MS })

  // Push only: this page opens the sensors itself. Hidden, it asks only while one is on, so a withdrawal
  // still lands; decided per tick (the latest render's callback), since a changing option restarts the poll.
  const sensorOn = camera.running || camera.busy || headband.connected || headband.phase !== 'idle'
    || stations.some(s => s.running)
  // Nor while the sidecar's answer (push status) is recent: one asker per lesson, until the sidecar goes quiet.
  const sidecarRecent = () => performance.now() - sidecarAt.current < sidecarTrustMs.current
  usePoll(() => ((document.hidden && !sensorOn) || sidecarRecent() ? null : checkPermits()),
    { intervalMs: PUSH_POLL_MS, enabled: headband.pushMode === true, pauseWhenHidden: false })
  // Whatever the poll would decide: a consent change in this browser, or a batch newly declined (the status poll).
  const askPermitsNow = useEffectEvent(() => { checkPermits() })
  useEffect(() => {
    if (headband.pushMode !== true) return
    return onPermitsChanged(() => askPermitsNow())
  }, [headband.pushMode])

  // Discover stations (auto-select a single one), retried until non-empty. A
  // failed read applies nothing, so `stationId` never falls back to `default`.
  useEffect(() => {
    // `available` is null under push (not probed), so don't gate on falsiness alone.
    if (!headband.available && !headband.pushMode) return
    let alive = true
    let retry = null
    const discover = () => {
      // Under push the backend can't reach the sidecar, so ask it directly.
      const source = headband.pushMode
        ? sidecarDevices().then(list => ({ devices: list })).catch(() => null)
        : eegDevices().catch(() => null)
      source.then(d => {
        if (!alive) return
        // Not an answer: push threw (null), `eegDevices` swallowed a failure
        // (`error`), or a probe got nothing (`available === false`, not falsy).
        if (d === null || d.error || d.available === false) {
          retry = setTimeout(discover, DISCOVERY_RETRY_MS)
          return
        }
        const all = d?.devices || []
        if (all.length === 0) retry = setTimeout(discover, DISCOVERY_RETRY_MS)
        const face = all.find(x => x.kind === 'face')
        setCamera(c => ({ ...c, id: face?.device_id || null,
                          running: !!face?.running }))
        // Exclude cameras (not allow-list headbands), so a new headband kind isn't dropped.
        const list = all.filter(s => s.kind !== 'face')
        setStations(list)
        setStationId(prev => {
          if (prev && list.some(s => s.device_id === prev)) return prev
          if (list.length === 1) return list[0].device_id
          if (list.length === 0) return 'default'
          return null
        })
      })
    }
    discover()
    return () => { alive = false; clearTimeout(retry) }
  }, [headband.available, headband.pushMode])

  // Re-offers the session to the sidecar (initial handover, status poll, Use this tab). Taking it over drops
  // whatever another lesson had queued, and re-reads the devices: another tab may have switched the camera off.
  const recover = useCallback(() => {
    const sid = sessionIdRef.current
    if (!sid) return
    pushHandoff.current = pushHandoff.current
      .catch(() => {})
      .then(() => startPush(sid))
      .then(() => {
        setPush(p => ({ ...(p || {}), running: true, reachable: true, error: null }))
        ownSessions.current.add(sid)
        ownershipEpoch.current += 1
        setSidecarSession(sid)
        sidecarDevices().then(list => {
          const running = !!list.find(d => d.kind === 'face')?.running
          setCamera(c => (c.running === running ? c : { ...c, running }))
        }, () => {})
      })
      .catch(err => {
        // 409: the sidecar declined; re-offering won't change that.
        if (err?.status === 409) {
          setPush(p => ({ ...(p || {}), running: false, reachable: true, enabled: false }))
        }
      })
  }, [])

  // Stops one device, releases the push client if nothing else streams, and syncs camera and station
  // state, from the device list when it was read. `strict` throws on a stop that failed or left the
  // device listed as running, changing no state, so the caller can retry.
  const endPushDevice = async (deviceId, { strict = false } = {}) => {
    stoppingIds.current.add(deviceId)
    let failure = null
    const stopped = await deviceStop(deviceId).then(() => true, e => {
      stoppingIds.current.delete(deviceId)
      // The sidecar's 503: the stream stopped, but a disconnect of this device is still running.
      if (e?.status === 503) e.releasing = true
      failure = e
      return !!e?.releasing
    })
    // Any other failed stop changes no state; a `releasing` one reads the list, which shows its stream stopped.
    if (strict && failure && !failure.releasing) throw failure
    // Another tab's lesson keeps its delivery: read the list, release nothing.
    const { devices: list } = pushOwnerRef.current === 'elsewhere'
      ? await sidecarDevices().then(d => ({ devices: d }), () => ({ devices: null }))
      : await releasePushIfIdle(sessionIdRef.current)
    if (strict && failure && !list) throw failure
    const listed = list?.find(d => d.device_id === deviceId)
    // Still on, so no stop is under way: a later refusal may try again.
    if (listed?.running) stoppingIds.current.delete(deviceId)
    if (strict && listed?.running) throw failure || new Error(`${deviceId} is still running after its stop`)
    if (listed || stopped) {
      const running = !!listed?.running
      // The same array when nothing changed (a camera is no station): an unchanged list renders nothing.
      setStations(all => (all.some(s => s.device_id === deviceId && s.running !== running)
        ? all.map(s => (s.device_id === deviceId ? { ...s, running } : s)) : all))
    }
    if (list) {
      const face = list.find(d => d.kind === 'face')
      setCamera(c => ({ ...c, running: !!face?.running }))
    } else {
      // Unread list: the client was released, so nothing is delivered.
      setCamera(c => ({ ...c, running: false }))
    }
    if (strict && failure) throw failure
  }

  // Hardware ops: pull proxies via /api/eeg/muse/*, push calls the sidecar on
  // loopback. `rec` is passed in so pull can use a not-yet-rendered recorder.
  const makeHw = (rec, { strict = false } = {}) => headband.pushMode ? {
    // Hardware only; delivery starts with the `sessionId` effect. Marked running, so a stop that
    // fails later still leaves it for `enforcePermits` to find.
    begin:      async () => { await deviceStart(stationId)
                              setStations(list => list.map(s => (s.device_id === stationId ? { ...s, running: true } : s)))
                              return { ok: true, running: true } },
    disconnect: () => museDisconnect(stationId),
    scan:       () => museRefresh(stationId),
    connect:    (name) => museConnect(name, stationId),
    status:     () => museState(stationId),
    // Not `stopPush()`: that is global and would stop the camera's delivery too.
    end:        () => endPushDevice(stationId, { strict }),
  } : {
    // Stream up, nothing written: `armRecording` arms recording on the first question.
    begin:      () => rec.start({ record: false }),
    disconnect: () => apiFetch('/api/eeg/muse/disconnect',
                               { method: 'POST', body: { device_id: stationId } }),
    scan:       (sid) => apiFetch('/api/eeg/muse/refresh',
                                  { method: 'POST', body: { device_id: stationId, session_id: sid } }),
    connect:    (name, sid) => apiFetch('/api/eeg/muse/connect',
                                        { method: 'POST', body: { name, device_id: stationId, session_id: sid } }),
    // `null` for an unlanded read, matching push's throw; never `{}` ("nothing connected").
    status:     async () => {
      const st = await eegStatus(stationId)
      return st?.answered === false ? null : (st?.muse || {})
    },
    // `?.`: a racing Disconnect may already have dropped the recorder.
    end:        () => rec?.stop(),
  }

  // One scan-and-connect, reporting a reason rather than toasting. `run` is the
  // reconnect loop's cancel token, checked between steps, as is page unmount.
  const pairOnce = async (hw, activeSessionId, run = null) => {
    const cancelled = () => run?.cancelled === true || !pageAlive.current || headbandRefused()
    const phase = (p) => { if (!run) setHeadband(s => ({ ...s, phase: p })) }

    // Before the disconnect: it is global to the shared bridge device.
    if (cancelled()) return { ok: false, reason: 'cancelled' }

    // Adopt a live link (see linkAlive) rather than tearing it down and rebuilding.
    const already = await hw.status().catch(() => null)
    if (cancelled()) return { ok: false, reason: 'cancelled' }
    // Unlanded read: touch nothing, since the fall-through disconnects.
    if (already === null) return { ok: false, reason: 'status_unavailable' }
    if (linkAlive(already?.ingestion)) {
      clearTimeout(phaseTimer.current)
      setHeadband(s => ({ ...s, connected: true, phase: 'connected', reconnect: null,
                           deviceName: already.ingestion.active_muse_name || s.deviceName }))
      return { ok: true, adopted: true }
    }

    // Disconnect a previous session first, or the next connect throws BadStateError.
    await hw.disconnect().catch(() => {})
    await new Promise(r => setTimeout(r, 1500))
    if (cancelled()) return { ok: false, reason: 'cancelled' }

    phase('scanning')
    // session_id scopes the station reservation this scan claims.
    await hw.scan(activeSessionId)

    let devices = []
    // Unlanded reads are not empty scans: `status_unavailable`, not `no_device`.
    let scanAnswered = false
    for (let i = 0; i < 12; i++) {
      await new Promise(r => setTimeout(r, 1000))
      if (cancelled()) return { ok: false, reason: 'cancelled' }
      const st = await hw.status()
      if (st === null) continue
      scanAnswered = true
      devices = st?.ingestion?.muse_devices || []
      if (devices.length > 0) break
      // Stop early if Bluetooth itself is off.
      if (st?.ingestion?.bluetooth_enabled === false) return { ok: false, reason: 'bluetooth_off' }
    }
    if (!scanAnswered) return { ok: false, reason: 'status_unavailable' }
    if (devices.length === 0) return { ok: false, reason: 'no_device' }
    if (cancelled()) return { ok: false, reason: 'cancelled' }

    const target = devices[0]
    phase('connecting')
    setHeadband(s => ({ ...s, deviceName: target }))
    await hw.connect(target, activeSessionId)

    // The bridge connects asynchronously; poll for it.
    let connectAnswered = false
    for (let i = 0; i < 10; i++) {
      await new Promise(r => setTimeout(r, 1000))
      if (cancelled()) return { ok: false, reason: 'cancelled' }
      const st = await hw.status()
      if (st === null) continue
      connectAnswered = true
      if (st?.ingestion?.muse_connected) {
        clearTimeout(phaseTimer.current)
        setHeadband(s => ({ ...s, connected: true, phase: 'connected', reconnect: null }))
        return { ok: true }
      }
    }
    // `not_connected` asks for a power-cycle, so only when a read actually landed.
    return { ok: false, reason: connectAnswered ? 'not_connected' : 'status_unavailable' }
  }

  const disconnectHeadband = async (hw) => {
    clearTimeout(phaseTimer.current)
    poorStreak.current = 0
    // A teardown ends the drop-toast episode, so the next drop is announced.
    lastDropToast.current = 0
    dropAnnounced.current = false
    settlingSince.current = null
    // Its stream stopped and the sidecar is still letting it go (`endPushDevice`): shown off, then reported.
    const releasing = await hw.end().then(() => null, e => { if (!e?.releasing) throw e; return e })
    // Drop, not reuse: it closed over the old deviceId.
    setRecorder(null)
    delete window.AL_currentSessionId
    setHeadband(s => ({ ...s, connected: false, phase: 'idle', deviceName: null,
                         battery: null, reconnect: null, contactPoor: null }))
    if (releasing) throw releasing
  }

  const onReconnected = () => {
    if (reconnectRun.current) reconnectRun.current.cancelled = true
    reconnectRun.current = null
    settlingSince.current = null
    setHeadband(s => ({ ...s, connected: true, phase: 'connected', reconnect: null }))
    // Only after an announced drop.
    if (dropAnnounced.current) toast.success('Headband reconnected.')
  }

  // Scan + connect with backoff, only when the bridge isn't (two drivers would fight).
  const startFrontendReconnect = () => {
    // Refused: the withdrawal effect tears the link down instead.
    if (reconnectRun.current || headbandRefused()) return
    const run = { cancelled: false }
    reconnectRun.current = run
    ;(async () => {
      const hw = makeHw(recorderRef.current)
      const sid = sessionIdRef.current
      let ok = false
      // An unlanded status read ends the run without spending the headband budget.
      let unreachable = false
      for (let attempt = 1; attempt <= RECONNECT_ATTEMPTS && !run.cancelled; attempt++) {
        // Consts for the closures: the hooks compiler cannot build `attempt++` on a captured variable.
        const reconnect = { attempt, max: RECONNECT_ATTEMPTS, byBridge: false }
        const backoff = RECONNECT_BACKOFF_MS[attempt - 1]
        setHeadband(s => ({ ...s, phase: 'reconnecting', reconnect }))
        await new Promise(r => setTimeout(r, backoff))
        if (run.cancelled) break
        // It may have come back on its own; a settling link gets the grace
        // (shared with the telemetry poll via `settlingSince`), not a teardown.
        let st = await hw.status().catch(() => null)
        if (run.cancelled) break
        while (linkSettling(st?.ingestion)) {
          if (settlingSince.current == null) settlingSince.current = Date.now()
          if (Date.now() - settlingSince.current >= SETTLE_GRACE_MS) break
          await new Promise(r => setTimeout(r, RECONNECT_POLL_MS))
          if (run.cancelled) break
          st = await hw.status().catch(() => null)
        }
        if (!linkSettling(st?.ingestion)) settlingSince.current = null
        if (run.cancelled) break
        if (linkAlive(st?.ingestion)) { ok = true; break }
        // Service not answering: stop, as the check below does.
        if (st == null) { unreachable = true; break }
        const res = await pairOnce(hw, sid, run).catch(() => ({ ok: false }))
        if (run.cancelled) break
        if (res.ok) { ok = true; break }
        // Stop; the telemetry poll (kept mounted by `reconnecting`) restarts this on a landed read.
        if (res.reason === 'status_unavailable') { unreachable = true; break }
      }
      if (run.cancelled) return
      reconnectRun.current = null
      if (ok) {
        onReconnected()
        return
      }
      // Nothing established: no teardown (that would stop the telemetry watch).
      // `serverUnreachable` replaces the stale attempt count in the sub-line.
      if (unreachable) {
        setHeadband(s => ({ ...s, reconnect: { ...s.reconnect,
                                               serverUnreachable: true } }))
        return
      }
      // Full teardown, not a state reset: under pull the poller must stop too.
      await disconnectHeadband(hw)
      toast.error('The headband could not be reconnected.', {
        description: 'Check it is switched on and charged, then click Connect Headband.',
        duration: 15_000,
      })
    })()
  }

  // A link that dropped on its own: announce once, then watch for recovery.
  const onDropped = (ing) => {
    poorStreak.current = 0
    // A fresh settle grace per episode.
    settlingSince.current = null
    const byBridge = ing.reconnecting === true
    setHeadband(s => ({
      ...s, connected: false, phase: 'reconnecting', battery: null, contactPoor: null,
      reconnect: { attempt: ing.reconnect_attempt || 0, max: ing.reconnect_max_attempts || 0, byBridge },
    }))
    // Toast once per DROP_TOAST_MIN_MS, not per drop; the panel shows every one.
    const now = Date.now()
    if (now - lastDropToast.current >= DROP_TOAST_MIN_MS) {
      lastDropToast.current = now
      dropAnnounced.current = true
      toast.warning('The headband disconnected.', {
        description: 'Trying to reconnect. Check it is switched on and sitting on your head.',
        duration: 8_000,
      })
    } else {
      dropAnnounced.current = false
    }
    // Bridge isn't recovering: start this page's loop now.
    if (!byBridge && !reconnectRun.current) startFrontendReconnect()
  }

  // The poll calls the latest handlers without restarting; they read refs and setState.
  const handleDropped = useEffectEvent(ing => onDropped(ing))
  const handleReconnected = useEffectEvent(() => onReconnected())
  const takeOverReconnect = useEffectEvent(() => startFrontendReconnect())

  // Polls charge, contact and the BLE link in both modes (only the bridge reports
  // a drop), and keeps polling through `reconnecting`.
  const reconnecting = headband.phase === 'reconnecting'
  useEffect(() => {
    if (!(headband.connected || reconnecting) || !stationId) return
    let killed = false
    // `{ st }`, or null for a tick that did not land.
    const readState = () => {
      if (headband.pushMode) return museState(stationId).then(st => ({ st }))
      // The status poll reads this endpoint every few seconds; reuse its answer, except while
      // reconnecting, when the link coming back is what this poll is for.
      const recent = lastStatus.current
      const reuse = !reconnecting && recent?.stationId === stationId
        && Date.now() - recent.at < TELEMETRY_POLL_MS
      // `eegStatus` swallows failure into a fallback; an unlanded tick writes nothing.
      return (reuse ? Promise.resolve(recent.answer) : eegStatus(stationId))
        .then(answer => (answer?.answered === false ? null : { st: answer?.muse }))
    }
    const read = () => Promise.resolve()
      .then(readState)
      .then(got => {
        if (!got || killed) return
        const ing = got.st?.ingestion || {}
        const prev = headbandRef.current
        const pct = ing.battery_percent
        // typeof, not `pct || null`: 0% is a reading.
        const battery = typeof pct === 'number' ? pct : null

        // `=== false`, not falsiness: an absent field is not a drop. Recovery
        // needs EEG flowing (linkAlive), not only CONNECTED.
        const linkUp = linkAlive(ing)
        const dropped = ing.muse_connected === false

        if (prev.phase === 'reconnecting') {
          if (linkUp) {
            settlingSince.current = null
            handleReconnected()
            return
          }
          // A settling link is left alone until the grace expires.
          if (linkSettling(ing)) {
            if (settlingSince.current == null) settlingSince.current = Date.now()
            if (Date.now() - settlingSince.current < SETTLE_GRACE_MS) return
          } else {
            settlingSince.current = null
          }
          // Show the bridge's progress; once it gives up (or an older bridge never tries), take over.
          const bridgeTrying = ing.reconnecting === true
          if (bridgeTrying) {
            setHeadband(s => ({ ...s, reconnect: {
              attempt: ing.reconnect_attempt || 0,
              max: ing.reconnect_max_attempts || 0,
              byBridge: true,
            } }))
          } else if (!reconnectRun.current) {
            takeOverReconnect()
          }
          return
        }

        // A drop needs `phase: 'connected'`: under pull `connected` is true from
        // `/api/eeg/start`, before the scan, so it alone misreads pairing as a drop.
        if (dropped && prev.connected && prev.phase === 'connected') {
          handleDropped(ing)
          return
        }

        // Steady state: charge and debounced contact.
        const quality = contactQuality(ing)
        if (quality === 'poor') poorStreak.current += 1
        else poorStreak.current = 0
        const contactPoor = quality == null ? null : poorStreak.current >= CONTACT_POOR_STREAK
        setHeadband(s => ({ ...s, battery, contactPoor }))
      })
      .catch(() => {
        // Keep last known values; disconnect clears them.
      })
    read()
    const id = setInterval(read, reconnecting ? RECONNECT_POLL_MS : TELEMETRY_POLL_MS)
    return () => { killed = true; clearInterval(id) }
  }, [headband.connected, reconnecting, headband.pushMode, stationId])

  // "Stop trying": Disconnect's teardown, after an explicit bridge disconnect
  // (only a command cancels the bridge's own attempts).
  const cancelReconnect = async ({ strict = false } = {}) => {
    if (reconnectRun.current) reconnectRun.current.cancelled = true
    reconnectRun.current = null
    const hw = makeHw(recorderRef.current, { strict })
    await hw.disconnect().catch(() => {})
    await disconnectHeadband(hw)
  }

  // A refusal switches off what it refuses, adopted at load or started since; unknown stops nothing.
  // A failed stop changes no state, so the next answer retries it; a `releasing` one's stream stopped: shown off.
  // A pairing stops itself (`pairOnce`).
  const stopRefused = (sensor, stop, verdict, done) => {
    stop().then(() => {
      toast.warning(done, { description: blockedLine(sensor, verdict) })
    }, e => {
      console.error(`[permits] could not switch the ${sensor} off`, e)
      if (stopFailed.current[sensor]) return
      stopFailed.current[sensor] = true
      if (e?.releasing) {
        toast.warning(`The ${sensor} has stopped but is still being released.`,
          { description: blockedLine(sensor, verdict) })
        return
      }
      toast.error(`The ${sensor} could not be switched off.`,
        { description: `${blockedLine(sensor, verdict)} This retries on its own.` })
    })
  }
  const enforcePermits = useEffectEvent((answer) => {
    if (!headband.pushMode) return
    const live = headband.connected || headband.phase === 'reconnecting'
    const pairing = ['starting', 'scanning', 'connecting'].includes(headband.phase)
    const stationOn = (id) => stations.some(s => s.device_id === id && s.running) || (id === stationId && live)
    for (const id of stoppingIds.current) {
      if (!(id === camera.id ? camera.running : stationOn(id))) stoppingIds.current.delete(id)
    }
    if (!camera.running) stopFailed.current.camera = false
    if (!live && !pairing && !stations.some(s => s.running)) stopFailed.current.headband = false

    const v = permitVerdicts(answer)
    if (v.camera.refused && camera.id && camera.running && !stoppingIds.current.has(camera.id)) {
      const id = camera.id
      stopRefused('camera', () => endPushDevice(id, { strict: true }), v.camera, 'The camera was turned off.')
    }
    if (!v.headband.refused) return
    // The live station goes through Disconnect's teardown; a pairing's station is the pairing's to stop.
    const own = live || pairing ? stationId : null
    const others = stations.filter(s => s.running && s.device_id !== own && !stoppingIds.current.has(s.device_id))
      .map(s => s.device_id)
    const teardown = !live || stoppingIds.current.has(stationId) ? null
      : headband.phase === 'reconnecting' ? () => cancelReconnect({ strict: true })
        : () => disconnectHeadband(makeHw(recorderRef.current, { strict: true }))
    if (!teardown && others.length === 0) return
    // Marked now: Stop trying's teardown reaches `endPushDevice` only after its bridge disconnect.
    if (teardown) stoppingIds.current.add(stationId)
    // Every stop settles first; a real failure outranks "still being released", being the one retried.
    stopRefused('headband', () => Promise.allSettled([
      ...(teardown ? [teardown()] : []),
      ...others.map(id => endPushDevice(id, { strict: true })),
    ]).then(results => {
      const failures = results.filter(r => r.status === 'rejected').map(r => r.reason)
      if (failures.length) throw failures.find(e => !e?.releasing) || failures[0]
    }), v.headband, 'The headband was disconnected.')
  })
  useEffect(() => { enforcePermits(permits.answer) },
    [permits, camera.running, headband.connected, headband.phase, stations])

  // Closes the session; clearing `sessionId` takes the token back off the
  // sidecar. Hardware stays paired.
  const finishSession = async () => {
    setFinishing(true)
    await endSession(sessionIdRef.current).finally(() => {
      // The ref too, so a Generate right after Finish starts a new session rather than reusing this one.
      sessionIdRef.current = null
      setSessionId(null)
      setSessionCount(0)
      setData(null)
      setPhase('idle')
      setFinishing(false)
      delete window.AL_currentSessionId
    })
  }

  const creating = useRef(null)

  // The ref, not the state: a handler that just dropped a closed session must not get it back.
  const getOrCreateSession = async () => {
    if (sessionIdRef.current) return sessionIdRef.current
    if (creating.current) return creating.current
    creating.current = apiFetch('/api/sessions/start', { method: 'POST', body: { title: 'Adaptive Session' } })
      .then(s => { sessionIdRef.current = s.id; setSessionId(s.id); return s.id })
      .finally(() => { creating.current = null })
    return creating.current
  }

  // poll EEG status while connected; under push only its mode fields matter, so 30 s
  usePoll(async (stopped) => {
    const s = await eegStatus(stationId)
    if (stopped()) return
    // An unlanded tick writes nothing (`eegStatus` swallows failure into a
    // plausible-looking object). Drops belong to the telemetry poll.
    if (s.answered === false) return
    lastStatus.current = { stationId, at: Date.now(), answer: s }
    setHeadband(prev => ({
      ...prev,
      // `service` is null under push: the backend never probes the sidecar.
      pushMode: s.ingest_mode === 'push',
      // Authenticated, so never address-limited: a landed tick clears `probeRefused`.
      available: !!s.service,
      probeRefused: false,
      // Pull only (the backend's poller), and not while reconnecting: the
      // telemetry poll alone owns that transition.
      ...(s.ingest_mode === 'push' || prev.phase === 'reconnecting' ? {} : {
        connected: !!s.poller?.running,
      }),
      samples:   s.poller?.samples || 0,
      lastTs:    s.poller?.last_ts || null,
      // Pull only (telemetry covers push); typeof so 0% is a reading.
      ...(s.ingest_mode === 'push' ? {} : {
        battery: typeof s.muse?.ingestion?.battery_percent === 'number'
          ? s.muse.ingestion.battery_percent : null,
        withheld: !!s.poller?.running && !!s.poller?.withheld,
      }),
    }))
  }, { intervalMs: headband.pushMode ? PUSH_POLL_MS : PULL_STATUS_POLL_MS,
       key: `${sessionId}:${stationId}`, enabled: !!sessionId,
       // Under pull this request refreshes the station pairing; unpolled 120 s, it is released.
       pauseWhenHidden: !!headband.pushMode })

  // Push only: hand the session and token to the sidecar, and take them back
  // at the end. Under pull the poller is the writer; both would double-write.
  useEffect(() => {
    if (!sessionId || !headband.pushMode) return
    let killed = false

    // Retried: the sidecar often starts after this page.
    let attempt = null
    const handOver = () => {
      // Chained with the cleanup's stop so they stay ordered.
      pushHandoff.current = pushHandoff.current
        .catch(() => {})
        .then(() => startPush(sessionId))
        .then(() => {
          if (killed) return
          setPush(p => ({ ...(p || {}), running: true, reachable: true, error: null }))
          // Taken over now, not at the next poll: a tab that saw another lesson's would say so until then.
          ownSessions.current.add(sessionId)
          ownershipEpoch.current += 1
          setSidecarSession(sessionId)
        })
        .catch(err => {
          if (killed) return
          if (err.status === 409) {
            // PUSH_ENABLED is off on the sidecar; retrying can't change that.
            setPush(p => ({ ...(p || {}), running: false, reachable: true, enabled: false,
                            error: String(err.message || err) }))
            return
          }
          // Ordinary before the local app starts; recorded so the panel can say why.
          setPush(p => ({ ...(p || {}), running: false, reachable: false, error: String(err.message || err) }))
          attempt = setTimeout(handOver, PUSH_RETRY_MS)
        })
      return pushHandoff.current
    }
    handOver()

    // Re-hand a refreshed token (hourly expiry), or pushes start 401ing.
    const { data: sub } = supabase.auth.onAuthStateChange((event, session) => {
      // Every tab hears the refresh; re-handing another tab's lesson's delivery would take it back.
      if (event !== 'TOKEN_REFRESHED' || killed || pushOwnerRef.current === 'elsewhere') return
      // The callback's token: `getSession()` here deadlocks on the auth lock.
      const token = session?.access_token
      if (!token) return
      pushHandoff.current = pushHandoff.current
        .catch(() => {})
        .then(() => (killed ? null : startPush(sessionId, token)))
        .catch(() => {})
    })

    // Cleanup doesn't run on a tab close; `pagehide` (bfcache- and mobile-safe) does. Both stop this lesson
    // only: by the sidecar's check, and here too for an older one, which ignores the session it is given.
    const onPageHide = (e) => {
      if (pushOwnerRef.current !== 'elsewhere') stopPushOnUnload(sessionId)
      reloadIfRestored(e)
    }
    window.addEventListener('pagehide', onPageHide)

    return () => {
      killed = true
      clearTimeout(attempt)
      window.removeEventListener('pagehide', onPageHide)
      sub?.subscription?.unsubscribe()
      const leaving = pushOwnerRef.current !== 'elsewhere'
      // Released by this stop; kept, the finished lesson's id would read as another tab's until the next poll.
      if (leaving) setSidecarSession(null)
      // Chained behind any in-flight start (StrictMode remount).
      pushHandoff.current = pushHandoff.current
        .catch(() => {})
        .then(() => (leaving ? stopPush(sessionId) : null))
        .catch(() => {})
      setPush(null)
    }
  }, [sessionId, headband.pushMode])

  // Push only, once the sidecar holds this session and a headband streams; never camera-only.
  useEegStartReport(!!(headband.pushMode && headband.connected && push?.running && pushOwner !== 'elsewhere'), sessionId)

  // Delivery counts for the panel and recording chip; a slower poll. Before a lesson too, so a tab with none
  // knows the sensors are another tab's lesson's and leaves them on when it closes.
  useEffect(() => {
    if (!headband.pushMode) return
    let killed = false
    const tick = () => {
      const epoch = ownershipEpoch.current
      return pushStatus().then(d => {
        if (killed) return
        const named = d.session_id ?? null
        const winding = named !== null && named !== sessionIdRef.current && ownSessions.current.has(named)
        if (epoch === ownershipEpoch.current) setSidecarSession(winding ? null : named)
        // With no lesson, whose lesson it is is all this page needs; counts and messages are a lesson's.
        if (!sessionId) return
        // Another tab's lesson's counts are not this one's: no labels, and no baseline to diff against later.
        const theirs = !!d.session_id && d.session_id !== sessionIdRef.current
        const prev = theirs ? null : lastRecorded.current
        const now = theirs ? null : d.recorded || {}
        // The first poll is only a baseline, so a reload doesn't list stale channels.
        const labels = prev && now ? CHANNEL_LABELS
          .filter(([key]) => (now[key] || 0) > (prev[key] || 0))
          .map(([, label]) => label) : []
        // The same array, and below the same object, when nothing changed: such a read renders nothing.
        setRecording(r => (r.join() === labels.join() ? r : labels))
        lastRecorded.current = now
        // Newly declined, so the latest answer may be stale (a withdrawal): ask now, unless the sidecar does
        // (`checks_on_refusal`). A result gone stale (null) keeps the one before it, or a gap would ask again.
        const results = Object.fromEntries(Object.entries(d.last_result || {}).filter(([, r]) => r != null))
        if (!d.checks_on_refusal
            && CHANNEL_LABELS.some(([k]) => results[k] === 'declined' && lastResults.current[k] !== 'declined')) {
          askPermitsNow()
        }
        lastResults.current = { ...lastResults.current, ...results }
        // A restarted sidecar has no token; skip `enabled: false` (config, would 409).
        if (d.enabled !== false && !d.running) recover()
        // The sidecar's answer goes to the permit state, not here: its age differs on every read.
        const { permits: sidecarAnswer, permits_age_seconds: sidecarAge, permits_check_seconds: sidecarCheck,
                ...status } = d
        // Reachable and running are separate claims; `answered`: only a status says whether it reports results.
        setPush(p => {
          const next = { ...(p || {}), ...status, reachable: true, running: !!d.enabled && !!d.running, answered: true }
          return JSON.stringify(next) === JSON.stringify(p) ? p : next
        })
        if (sidecarAnswer && typeof sidecarAge === 'number') takeSidecarPermits(sidecarAnswer, sidecarAge, sidecarCheck)
      })
      .catch(() => {
        if (killed || !sessionId) return
        setPush(p => ({ ...(p || {}), reachable: false, running: false }))
        setRecording([])
        // The next successful poll is a fresh baseline.
        lastRecorded.current = null
        // A failed read says nothing about whose lesson it is: one last seen as another tab's stays theirs.
        if (pushOwnerRef.current !== 'elsewhere') recover()
      })
    }
    tick()
    const id = setInterval(tick, PUSH_STATUS_POLL_MS)
    return () => { killed = true; clearInterval(id) }
  }, [sessionId, headband.pushMode, recover])

  // Poll EEG debug snapshot (dev only)
  useEffect(() => {
    if (!EEG_DEBUG) return
    // Wait until pushMode is known.
    if (headband.pushMode === undefined) return
    // Under push, read the sidecar directly; same shape.
    const poll = () => (headband.pushMode
      ? sidecarDebug(stationId || 'default')
      : apiFetch(`/api/eeg/debug${stationId ? `?device_id=${encodeURIComponent(stationId)}` : ''}`))
      .then(d => setEegDebug(d))
      .catch(() => setEegDebug(null))
    poll()
    debugTimer.current = setInterval(poll, 1500)
    return () => clearInterval(debugTimer.current)
  }, [stationId, headband.pushMode])

  // Clears the obsolete localStorage accuracy cache on shared computers.
  useEffect(() => {
    if (!user?.id) return
    localStorage.removeItem(`accuracyStats_${user.id}`)
  }, [user])

  useEffect(() => { localStorage.setItem('adaptive_mode', mode) }, [mode])

  const toggleCamera = async () => {
    // Push only; guarded here as well as by the disabled button.
    if (!camera.id || !headband.pushMode || camera.busy) return
    // Busy first, so a double click can't start it twice while the permit is re-read.
    setCamera(c => ({ ...c, busy: true }))
    if (!camera.running) {
      const fresh = permitVerdicts(await checkPermits()).camera
      if (!fresh.allowed || !pageAlive.current) {
        setCamera(c => ({ ...c, busy: false }))
        if (pageAlive.current) toast.error("The camera can't be turned on.", { description: blockedLine('camera', fresh) })
        return
      }
    }
    const toggled = camera.running
      // Shared helper, so the sidecar doesn't keep the student's token.
      ? endPushDevice(camera.id)
      // No session: frames are dropped until a lesson consumes them.
      // Refused while starting: `enforcePermits` stops it once `running` lands.
      : deviceStart(camera.id).then(() => setCamera(c => ({ ...c, running: true })))
    await toggled
      .catch(e => {
        console.error('[camera]', e)
        toast.error('The camera could not be switched.', {
          description: errorDetail(e),
        })
      })
      .finally(() => setCamera(c => ({ ...c, busy: false })))
  }

  const toggleHeadband = async () => {
    if (!stationId) return
    if (headband.phase === 'reconnecting') {
      await cancelReconnect()
      return
    }
    // Only Connect is gated. `starting` first, so a double click can't pair twice while the permit is re-read.
    if (headband.pushMode && !headband.connected) {
      setHeadband(s => ({ ...s, phase: 'starting' }))
      const fresh = permitVerdicts(await checkPermits()).headband
      if (!fresh.allowed || !pageAlive.current) {
        setHeadband(s => ({ ...s, phase: 'idle' }))
        if (pageAlive.current) toast.error("The headband can't be connected.", { description: blockedLine('headband', fresh) })
        return
      }
    }
    // Pull needs a session (its reservation is per session_id); a failure here
    // resets the button and alerts.
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
    // A local, since `setRecorder()` lands only on the next render.
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

    // A failed pairing: stop what `begin` started, reset, and say why.
    const pairingFailed = (outcome) => {
      // `begin` started a stream (pull: the backend poller, which never stops on its own).
      // Dropped, not reused: a stopped recorder has removed its `pagehide` listener.
      Promise.resolve().then(() => hw.end())
        .catch(e => console.error('[headband] could not stop after a failed pairing', e))
      setRecorder(null)
      setHeadband(s => ({ ...s, phase: 'idle', deviceName: null }))
      // Long dwell: these are instructions. An unlanded read blames the check,
      // never the headband.
      if (outcome.reason === 'cancelled') {
        // A page gone needs no toast; a refusal (`pairOnce` read `permitsRef`) says why, once.
        if (pageAlive.current && headbandRefused()) {
          toast.warning("The headband wasn't connected.",
            { description: blockedLine('headband', permitVerdicts(permitsRef.current).headband) })
        }
      } else if (outcome.reason === 'status_unavailable') {
        toast.error('Could not reach the EEG service.', {
          description: 'Your headband was not touched. This usually clears on its own — '
            + 'click Connect Headband again in a moment.',
          duration: 12_000,
        })
      } else if (outcome.reason === 'bluetooth_off') {
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
    }

    setHeadband(s => ({ ...s, phase: 'starting' }))
    // One chain: any failure here, a throw included, resets the button and alerts once.
    await Promise.resolve()
      .then(() => hw.begin(activeSessionId))
      .then(res => {
        if (!res?.ok && !res?.running) throw new Error(res?.error || 'Could not start EEG session')
        return pairOnce(hw, activeSessionId)
      })
      .then(outcome => { if (!outcome.ok) pairingFailed(outcome) })
      .catch(e => {
        console.error('[headband]', e)
        clearTimeout(phaseTimer.current)
        setHeadband(s => ({ ...s, phase: 'idle', deviceName: null }))
        toast.error('The headband could not connect.', {
          description: errorDetail(e),
        })
      })
  }

  // Pull only: arms the poller's `record` flag from the first question, replacing
  // a recorder bound to an earlier session. Push keys delivery on `sessionId`.
  const armRecording = async (activeSessionId) => {
    if (headband.pushMode || !headband.connected || !stationId) return
    let rec = recorder
    if (!rec || rec.sessionId !== activeSessionId) {
      // `stop()` also removes the old recorder's `pagehide` listener.
      if (rec) await rec.stop()
      rec = createSignalRecorder({ sessionId: activeSessionId, deviceId: stationId })
      setRecorder(rec)
      recorderRef.current = rec
      window.AL_currentSessionId = activeSessionId
    }
    const res = await rec.start({ record: true })
    if (!res?.ok) console.error('[headband] could not start recording', res?.error)
  }

  const fetchQuestion = () => {
    setPhase('loading'); setError(false)
    return getOrCreateSession()
      .then(activeSessionId => {
        // Not awaited: a recording problem must not withhold a question.
        armRecording(activeSessionId).catch(e => console.error('[headband]', e))
        // The duration clock starts on the first question only.
        setSessionStartedAt(prev => prev ?? Date.now())

        // No user id: the backend takes the student from the bearer.
        const params = new URLSearchParams({ bias: String(bias) })
        if (mode === 'class' && classId) params.set('class_id', classId)
        else if (grade)                   params.set('grade', grade)
        params.set('session_id', activeSessionId)

        return apiFetch(`/api/generate-question?${params.toString()}`)
      })
      .then(json => {
        if (!json?.question_text) throw new Error('Invalid response')
        setData(json)
        setActiveButton(null); setSelectedAnswer(null)
        setPhase('question')
      })
      .catch(err => {
        console.error(err); setError(true); setPhase('idle')
      })
  }

  const handleSubmit = async () => {
    const isCorrect = JSON.stringify(data.answer_options[selectedAnswer]) === JSON.stringify(data.correct_answer)
    setCorrect(isCorrect)
    setPhase('result')

    const answer = { questionId: data?.id, selectedIndex: selectedAnswer, correct: isCorrect }
    // Counted while in flight, so leaving the page does not end the session under it.
    const saving = (p) => { answersInFlight.current += 1; return p.finally(() => { answersInFlight.current -= 1 }) }
    let res = await saving(recordAnswer({ sessionId: sessionIdRef.current, ...answer }))
    if (res?.ended) {
      // Closed server-side while this page held it: the answer goes into a fresh session.
      sessionIdRef.current = null
      setSessionId(null)
      const fresh = await getOrCreateSession().catch(e => { console.error('[session]', e); return null })
      // Pull: the poller was writing the closed session; push re-hands over on `sessionId`.
      if (fresh) armRecording(fresh).catch(e => console.error('[headband]', e))
      res = await saving(recordAnswer({ sessionId: fresh, ...answer }))
      if (res?.ended) {
        // Refused twice: say so, as every other unsaved answer does.
        toast.error('That answer could not be saved.')
        res = null
      }
    }
    if (res) {
      // Counted only once the answer is stored; failures already toasted.
      setSessionCount(n => n + 1)
      // The topic the backend attributed, never a local guess.
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

  // Under push there is no poller count: EEG's `push.recorded`, not heart's, which the camera can supply too.
  const headbandSamples = headband.pushMode ? (push?.recorded?.cognitive || 0) : headband.samples
  // Readings the backend refused (whole batch, or one by one): lost, and nowhere else on screen. Not another
  // tab's lesson's: every count the status carries is for the lesson the sidecar delivers for.
  const pushLost = pushOwner === 'elsewhere' ? 0 : ['rejected', 'malformed']
    .flatMap(k => Object.values(push?.[k] || {}))
    .reduce((a, b) => a + (Number(b) || 0), 0)
  // One state per channel drives every badge and sentence: fresh 'recorded' or 'declined', 'waiting' (nothing
  // back yet), 'stale' (nothing back lately: an outage or a backoff) or 'elsewhere' (another tab's lesson).
  // An older sidecar reads 'recorded'.
  const reportsResults = push?.last_result != null
  const channelState = (key) => {
    if (pushOwner === 'elsewhere') return 'elsewhere'
    if (!push?.answered) return headband.pushMode ? 'waiting' : 'recorded'
    if (!reportsResults) return 'recorded'
    if (push.last_result[key]) return push.last_result[key]
    const seen = (Number(push.recorded?.[key]) || 0) + (Number(push.declined?.[key]) || 0)
    return seen > 0 ? 'stale' : 'waiting'
  }
  // Under pull a withheld poller writes neither headband channel; the switch is the only reason it pauses.
  const pullWithheld = !headband.pushMode && headband.connected && headband.withheld
  const declined = pullWithheld
    ? CHANNEL_LABELS.filter(([key]) => key !== 'face')
      .map(([key, label]) => ({ key, label, reason: PERMIT_REASONS.switched_off }))
    : CHANNEL_LABELS
      .filter(([key]) => channelState(key) === 'declined')
      .map(([key, label]) => ({ key, label, reason: push?.declined_reason?.[key] || 'no reason given' }))
  const pushRunning = !!(push?.reachable && push?.running)
  // The headband card speaks for the headband's EEG only, as the camera card does for the camera.
  const eegState = headband.pushMode ? channelState('cognitive') : pullWithheld ? 'declined' : 'recorded'
  const headbandRecording = pushRunning && eegState === 'recorded'
  const headbandWaiting = pushRunning && eegState === 'waiting'
  const cameraState = channelState('face')
  const cameraDeclined = declined.find(d => d.key === 'face')
  // Gates switching on only (push); Off, Disconnect and Stop trying never read it.
  const verdicts = permitVerdicts(permits.answer)
  const connectedLine = {
    declined: 'Connected, but your headband readings are not being saved, so your teacher cannot see them.',
    waiting: 'Connected. Waiting for the first readings to be saved.',
    stale: 'Connected, but no readings have been saved recently, so your teacher cannot see them live.',
    elsewhere: 'Connected. Your readings go to the lesson in another tab or window.',
  }[eegState]
    || `${headbandSamples} samples sent · teacher can see your focus & stress live`

  const activeClass = classes.find(c => c.id === classId)
  // The grade the backend serves: '' is none set anywhere, so its default; undefined is not
  // known, since with no grade chosen the backend reads the profile this page could not.
  const chosenGrade = mode === 'class' ? (activeClass?.grade_level || savedGrade) : grade
  const effectiveGrade = chosenGrade || (profileRead ? '' : undefined)
  // What this grade is served, plus anything attempted.
  const gradeTopics = useGradeTopics(effectiveGrade === undefined ? undefined : effectiveGrade || null)
  const shownTopics = topicsToShow(gradeTopics,
    TOPICS.filter(t => (accuracyStats.subjects[t]?.attempts ?? 0) > 0))
  const biasLabel = bias === -1 ? 'Easier' : bias === 1 ? 'Harder' : 'Auto'

  return (
    <div className="p-6 lg:p-8 pb-12">
      <m.div initial={{ opacity: 0, y: -10 }} animate={{ opacity: 1, y: 0 }} className="mb-4">
        <h1 className="text-3xl font-black text-gray-900 dark:text-white">🧠 AI Adaptive Practice</h1>
        <p className="text-gray-500 dark:text-gray-400 mt-1">The AI picks your weakest topic and generates a custom question.</p>
      </m.div>

      {/* The sidecar delivers one lesson at a time; the newest tab took it, and only this button takes it back. */}
      {headband.pushMode && sessionId && pushOwner === 'elsewhere' && (
        <div role="status"
          className="mb-4 rounded-2xl border border-amber-200 dark:border-amber-800 bg-amber-50 dark:bg-amber-900/30 p-4 flex items-center gap-3 flex-wrap">
          <p className="flex-1 min-w-0 text-sm font-bold text-amber-800 dark:text-amber-200">
            This lesson is also open in another tab or window. Readings go there.
          </p>
          <button onClick={recover}
            className="px-4 py-2 rounded-xl text-sm font-bold bg-amber-600 hover:bg-amber-700 text-white shadow">
            Use this tab
          </button>
        </div>
      )}

      {/* HEADBAND PANEL */}
      <m.div initial={{ opacity: 0, y: -6 }} animate={{ opacity: 1, y: 0 }}
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
            {/* Not during a refusal: `available` is stale then, and "ready" would be unconfirmed. */}
            {!headband.connected && headband.phase !== 'reconnecting' && headband.available && !headband.probeRefused && <span className="text-[10px] font-bold px-2 py-0.5 bg-indigo-100 dark:bg-indigo-900/40 text-indigo-700 dark:text-indigo-300 rounded-full">ready</span>}
            {/* A refused or unreached probe says neither up nor down; this names the check. */}
            {(headband.probeRefused || headband.probeUnreachable) && !headband.serviceError && !headband.connected && !headband.pushMode && <span className="text-[10px] font-bold px-2 py-0.5 bg-amber-100 dark:bg-amber-900/40 text-amber-700 dark:text-amber-300 rounded-full">status unavailable</span>}
            {/* Follows the sentence, qualifier included. */}
            {headband.serviceError && !headband.connected && !headband.pushMode && <span className="text-[10px] font-bold px-2 py-0.5 bg-amber-100 dark:bg-amber-900/40 text-amber-700 dark:text-amber-300 rounded-full">{(headband.probeRefused || headband.probeUnreachable) ? 'needs setup · unchecked' : 'needs setup'}</span>}
            {/* `=== false`: null is "no probe has answered yet". */}
            {!headband.probeRefused && !headband.probeUnreachable && !headband.serviceError && headband.available === false && !headband.pushMode && <span className="text-[10px] font-bold px-2 py-0.5 bg-gray-100 dark:bg-gray-800 text-gray-600 dark:text-gray-300 rounded-full">offline</span>}
            {headband.pushMode && <span className="text-[10px] font-bold px-2 py-0.5 bg-gray-100 dark:bg-gray-800 text-gray-600 dark:text-gray-300 rounded-full">on your device</span>}
            {/* EEG only. Grey while EEG has not come back yet, and only with a headband to wait on; amber when not saving. */}
            {headband.pushMode && headbandWaiting && headband.connected && (
              <span className="text-[10px] font-bold px-2 py-0.5 bg-gray-100 dark:bg-gray-800 text-gray-600 dark:text-gray-300 rounded-full">waiting to save</span>
            )}
            {headband.pushMode && eegState === 'elsewhere' && headband.connected && (
              <span className="text-[10px] font-bold px-2 py-0.5 bg-gray-100 dark:bg-gray-800 text-gray-600 dark:text-gray-300 rounded-full">in another tab</span>
            )}
            {headband.pushMode && push && !headbandRecording && !headbandWaiting && eegState !== 'elsewhere'
             && (push.reachable === false || push.enabled === false || push.running === false || reportsResults) && (
              <span className="text-[10px] font-bold px-2 py-0.5 bg-amber-100 dark:bg-amber-900/40 text-amber-700 dark:text-amber-300 rounded-full">not recording</span>
            )}
            {headband.pushMode && headbandRecording && (
              <span className="text-[10px] font-bold px-2 py-0.5 bg-emerald-100 dark:bg-emerald-900/40 text-emerald-700 dark:text-emerald-300 rounded-full">● RECORDING</span>
            )}
            {headband.pushMode && pushLost > 0 && (
              <span className="text-[10px] font-bold px-2 py-0.5 bg-amber-100 dark:bg-amber-900/40 text-amber-700 dark:text-amber-300 rounded-full">
                {pushLost} {pushLost === 1 ? 'reading' : 'readings'} not saved
              </span>
            )}
          </p>
          <p className="text-[11px] text-gray-600 mt-0.5 dark:text-gray-400">
            {headband.phase === 'scanning'   && '🔍 Scanning for Muse headbands via Bluetooth...'}
            {headband.phase === 'connecting' && `🔗 Connecting to ${headband.deviceName || 'headband'}...`}
            {headband.phase === 'starting'   && 'Starting EEG session...'}
            {/* Attempt 0 (the bridge's first backoff) isn't shown. Names an unreachable server here, since the health branches are idle-only. */}
            {headband.phase === 'reconnecting' && (
              headband.reconnect?.serverUnreachable
                ? "🔄 The headband disconnected — and the server can't be reached, so we can't try again yet. This carries on by itself as soon as it answers."
                : `🔄 The headband disconnected — reconnecting${
                    headband.reconnect?.attempt > 0 && headband.reconnect?.max > 0
                      ? ` (attempt ${headband.reconnect.attempt} of ${headband.reconnect.max})` : ''}…`
            )}
            {headband.phase === 'connected'  && connectedLine}
            {headband.phase === 'idle' && (
              headband.connected
                ? connectedLine
                : headband.pushMode
                  ? (!verdicts.headband.allowed
                      ? blockedLine('headband', verdicts.headband)
                    : push && push.enabled === false
                      ? 'The app on this computer is running but is not set up to record (PUSH_ENABLED is off). Nothing is being saved for this session.'
                    : push && push.reachable === false
                      ? 'The app on this computer is not running, so nothing is being recorded. Start it and this will change on its own.'
                      // No count here: a sum over channels climbs with the camera's readings while no headband is on.
                      : 'Turn on your Muse S headband, then click Connect. It pairs through the app on this computer.')
                  // Most specific known fact first: a stated config error, qualified
                  // (never erased) when the probe was since refused or unreached.
                  : headband.serviceError && headband.probeUnreachable
                  ? "The EEG service was running but not set up to use the headband when we last checked — and the server can't be reached right now, so that hasn't been re-checked. Restarting it will not help; this needs whoever set up this computer."
                  : headband.serviceError && headband.probeRefused
                  ? 'The EEG service was running but not set up to use the headband when we last checked, and we could not re-check just now. Restarting it will not help — this needs whoever set up this computer.'
                  : headband.serviceError
                  ? 'The EEG service is running but is not set up to use the headband. Restarting it will not help — this needs whoever set up this computer.'
                  // A known (answered) outage during a refusal keeps its fix-it
                  // sentence, qualified, so disabled Connect still has a reason.
                  : headband.probeRefused && headband.available === false && !headband.probeUnreachable
                  ? 'EEG service was not reachable when we last checked, and we could not re-check just now. Make sure the EEGResearch backend is running on port 8001.'
                  // Unknown or last seen up: no claim about the headband.
                  : headband.probeRefused
                  ? 'Could not check the EEG service just now. That says nothing about your headband — the check runs again on its own.'
                  : headband.available
                  ? 'EEG service ready. Turn on your Muse S headband then click Connect.'
                  // Our own backend was unreachable, so the sidecar was never probed.
                  : headband.probeUnreachable
                  ? "Couldn't reach the server, so the headband service hasn't been checked. This usually clears on its own."
                  // The first probe is still in flight (it has no timeout).
                  : headband.available === null
                  ? 'Checking the EEG service…'
                  // Only with an answer saying the sidecar is down.
                  : 'EEG service not reachable on port 8001. Make sure the EEGResearch backend is running.'
            )}
          </p>
          {/* Only on `=== true`: null is "not measured yet". */}
          {headband.connected && headband.contactPoor === true && (
            <p className="text-[11px] font-bold text-amber-700 dark:text-amber-300 mt-1">
              ⚠ Adjust the headband so the sensors sit flat against your skin — the reading is weak.
            </p>
          )}
          {/* The backend's reason, verbatim: consent, the school year or a recording switch can each be it. */}
          {(headband.pushMode || pullWithheld) && declined.length > 0 && (
            <p role="status" className="text-[11px] font-bold text-amber-700 dark:text-amber-300 mt-1">
              ⚠ Not being saved: {declined.map(d => `${d.label} (${d.reason})`).join(' · ')}
            </p>
          )}
        </div>
        {/* Only with more than one headband registered. */}
        {stations.length > 1 && !headband.connected && (
          // aria-label: some screen readers don't announce the placeholder option.
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
        {/* Only with a reading; `!= null` lets 0% through. */}
        {headband.connected && headband.battery != null && (
          // aria-label too: a title isn't reliably read or shown on touch.
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
        {/* Not gated on sessionId (created lazily); `available` is null under push. */}
        <button onClick={toggleHeadband}
          disabled={(!headband.available && !headband.pushMode) || !stationId || ['starting','scanning','connecting'].includes(headband.phase)
            || (headband.pushMode && headband.phase === 'idle' && !headband.connected && !verdicts.headband.allowed)}
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
      </m.div>

      {/* Disabled under pull: only the push endpoint writes `face_signals`. */}
      {camera.id && (
        <m.div initial={{ opacity: 0, y: -6 }} animate={{ opacity: 1, y: 0 }}
          className="mb-6 rounded-2xl bg-white dark:bg-gray-900 border border-gray-100 dark:border-gray-800 p-4 flex items-center gap-4 shadow-sm">
          <div className="w-11 h-11 rounded-xl bg-fuchsia-600 grid place-items-center text-white text-lg">📷</div>
          <div className="flex-1 min-w-0">
            {/* RECORDING needs a session, not just a running camera. */}
            <p className="font-bold text-sm flex items-center gap-2">
              Camera
              {camera.running && sessionId && cameraState === 'recorded'
                ? <span className="text-[10px] font-bold px-2 py-0.5 bg-emerald-100 dark:bg-emerald-900/40 text-emerald-700 dark:text-emerald-300 rounded-full">● RECORDING</span>
                : camera.running && sessionId && cameraState === 'waiting'
                  ? <span className="text-[10px] font-bold px-2 py-0.5 bg-gray-100 dark:bg-gray-800 text-gray-600 dark:text-gray-300 rounded-full">on, waiting to save</span>
                : camera.running && cameraState === 'elsewhere'
                  ? <span className="text-[10px] font-bold px-2 py-0.5 bg-gray-100 dark:bg-gray-800 text-gray-600 dark:text-gray-300 rounded-full">on, in another tab</span>
                : camera.running
                  ? <span className="text-[10px] font-bold px-2 py-0.5 bg-amber-100 dark:bg-amber-900/40 text-amber-700 dark:text-amber-300 rounded-full">on, not recording</span>
                  : <span className="text-[10px] font-bold px-2 py-0.5 bg-gray-100 dark:bg-gray-800 text-gray-600 dark:text-gray-300 rounded-full">off</span>}
            </p>
            <p className="text-[11px] text-gray-600 mt-0.5 dark:text-gray-400">
              {!headband.pushMode
                ? 'The camera records through the app on this computer, which this deployment is not set up for. Nothing would be saved.'
                : camera.busy
                  ? 'Starting the camera...'
                  : camera.running && sessionId && cameraDeclined
                    ? `The camera is on, but nothing from it is being saved (${cameraDeclined.reason}). No video is saved.`
                  : camera.running && sessionId && cameraState === 'waiting'
                    ? 'The camera is on. Waiting for its first readings to be saved. No video is saved.'
                  : camera.running && sessionId && cameraState === 'stale'
                    ? 'The camera is on, but nothing from it has been saved recently. No video is saved.'
                  : camera.running && cameraState === 'elsewhere'
                    ? 'The camera is on. Its readings go to the lesson in another tab or window. No video is saved.'
                  : camera.running && sessionId
                    ? 'Reading how you are finding the questions. No video is saved.'
                    : camera.running
                      ? 'The camera is on, but nothing is being recorded until you start a lesson. No video is saved.'
                      : !verdicts.camera.allowed
                        ? blockedLine('camera', verdicts.camera)
                        : 'Turn on to read how you are finding the questions. No video is saved.'}
            </p>
          </div>
          <button onClick={toggleCamera}
            disabled={!headband.pushMode || camera.busy || (!camera.running && !verdicts.camera.allowed)}
            className={`px-4 py-2 rounded-xl text-sm font-bold transition shadow disabled:opacity-50 disabled:cursor-not-allowed ${
              camera.running ? 'bg-rose-500 hover:bg-rose-600 text-white' : 'bg-fuchsia-600 hover:bg-fuchsia-700 text-white'
            }`}>
            {camera.busy ? 'Working...' : camera.running ? 'Turn off' : 'Turn on camera'}
          </button>
        </m.div>
      )}

      {/* A banner, not a modal, so it never blocks a question; dismiss keeps the preference. */}
      {timeUp && (
        <m.div initial={{ opacity: 0, y: -6 }} animate={{ opacity: 1, y: 0 }}
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
        </m.div>
      )}

      {/* Like the duration banner; "Keep going" keeps the goal. */}
      {goalReached && (
        <m.div initial={{ opacity: 0, y: -6 }} animate={{ opacity: 1, y: 0 }}
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
        </m.div>
      )}

      <div className="grid lg:grid-cols-3 gap-6">
        <div className="lg:col-span-2 space-y-4">
          {sessionCount > 0 && (
            <div className="flex gap-3 flex-wrap items-center">
              {/* Pull has no sidecar counts, so use headband.connected. */}
              <RecordingIndicator channels={
                headband.pushMode ? recording
                  : headband.connected && !pullWithheld ? ['Headband'] : []
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
                <m.div animate={{ y: [0, -8, 0] }} transition={{ duration: 2, repeat: Infinity, ease: 'easeInOut' }}
                  className="text-6xl mb-4 text-center">🚀</m.div>
                <h2 className="text-xl font-black text-gray-900 dark:text-white mb-2 text-center">Ready to practice?</h2>
                <p className="text-gray-500 dark:text-gray-400 text-sm mb-6 max-w-sm mx-auto text-center">
                  The AI analyses your performance across {(gradeTopics ?? TOPICS).length} topics and picks the one you need most.
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
                        {grade === '' && <option value="">
                          {profileRead === false ? 'Grade unknown' : profileRead ? 'Grade not set' : 'Loading grade…'}
                        </option>}
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
                        <p className="text-xs text-amber-600 mt-2 text-center">
                          ⚠️ Teacher hasn't set this class's grade yet — {savedGrade ? `using your grade, ${savedGrade}.`
                            : profileRead === false ? 'using your grade, which could not be loaded.'
                              : profileRead ? 'and your grade isn\'t set either.' : 'checking your grade…'}
                        </p>
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
                    Generating <strong>{biasLabel}</strong> questions
                    {effectiveGrade ? <> for <strong>{effectiveGrade}</strong></>
                      : effectiveGrade === '' ? ' (grade not set)'
                        : profileRead === false ? ' (grade unknown)' : ''}
                  </p>
                </div>

                {/* Questions this sitting: a dismissible goal, not a cap. */}
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
                      ? <>We will check in after <strong>{questionGoal}</strong>{durationMin ? <> instead of after {durationMin} minutes</> : null} — you can always keep going.</>
                      : durationMin
                        ? <>We will check in after <strong>{durationMin} minutes</strong> — you can always keep going.</>
                        : <>Practise for as long as you like.</>}
                  </p>
                </div>

                <div className="text-center">
                  <m.button onClick={fetchQuestion}
                    disabled={mode === 'class' && !classId}
                    whileHover={{ scale: 1.03 }} whileTap={{ scale: 0.97 }}
                    className="px-8 py-3 bg-gradient-to-r from-indigo-600 to-violet-600 text-white rounded-xl font-bold shadow-lg hover:from-indigo-700 hover:to-violet-700 transition disabled:opacity-50">
                    Generate Question
                  </m.button>
                  {error && <p className="text-rose-500 text-sm mt-4">⚠️ Generation failed — try again.</p>}
                </div>
              </div>
            )}

            {phase === 'loading' && (
              <div className="p-10 text-center">
                <m.div animate={{ rotate: 360 }} transition={{ duration: 1, repeat: Infinity, ease: 'linear' }}
                  className="w-12 h-12 border-4 border-indigo-600 border-t-transparent rounded-full mx-auto mb-4" />
                <p className="text-gray-500 dark:text-gray-400">AI is picking your topic...</p>
              </div>
            )}

            {(phase === 'question' || phase === 'result') && data && (
              <div className="p-7">
                <div className="flex gap-2 mb-4 flex-wrap">
                  {data.question_topic && (
                    <span className="text-xs font-bold px-2.5 py-1 bg-indigo-100 dark:bg-indigo-900/40 text-indigo-700 dark:text-indigo-300 rounded-full capitalize flex items-center gap-1">
                      {ICONS[data.question_topic]} {topicLabel(data.question_topic)}
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
                <CCSSBadge standard={data.ccss_standard} />

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
                      <m.button key={i} onClick={() => { if (phase !== 'question') return; setSelectedAnswer(i); setActiveButton(i) }}
                        disabled={phase === 'result'}
                        whileHover={phase === 'question' ? { x: 4 } : {}}
                        className={`w-full text-left p-4 rounded-xl border-2 transition-all duration-200 flex items-center gap-3 ${style}`}>
                        <span className="w-7 h-7 flex-shrink-0 rounded-lg bg-white dark:bg-gray-700 border border-gray-200 dark:border-gray-600 flex items-center justify-center text-sm font-bold">
                          {String.fromCharCode(65 + i)}
                        </span>
                        <span>{Array.isArray(opt) ? opt.join(', ') : opt}</span>
                        {isResult && isCorrectOpt && <span className="ml-auto text-green-500 text-lg">✓</span>}
                        {isResult && isWrong      && <span className="ml-auto text-rose-500 text-lg">✗</span>}
                      </m.button>
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
                  <m.div initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} className="space-y-3">
                    <div className={`p-4 rounded-xl text-center font-black text-lg ${correct ? 'bg-green-50 dark:bg-green-900/30 text-green-700 dark:text-green-300' : 'bg-rose-50 dark:bg-rose-900/30 text-rose-700 dark:text-rose-300'}`}>
                      {correct ? '🎉 Correct! Great job!' : '❌ Not quite — keep going!'}
                    </div>
                    <div className="flex items-center justify-center gap-2">
                      <span className="text-xs text-gray-600 dark:text-gray-400">Next question:</span>
                      <button onClick={() => setBias(-1)} className={`text-xs px-3 py-1.5 rounded-lg font-bold border ${bias === -1 ? 'bg-emerald-500 text-white border-emerald-500' : 'border-gray-200 dark:border-gray-700 text-gray-600 dark:text-gray-300'}`}>Easier</button>
                      <button onClick={() => setBias(0)}  className={`text-xs px-3 py-1.5 rounded-lg font-bold border ${bias === 0 ? 'bg-indigo-600 text-white border-indigo-600' : 'border-gray-200 dark:border-gray-700 text-gray-600 dark:text-gray-300'}`}>Auto</button>
                      <button onClick={() => setBias(1)}  className={`text-xs px-3 py-1.5 rounded-lg font-bold border ${bias === 1 ? 'bg-rose-500 text-white border-rose-500' : 'border-gray-200 dark:border-gray-700 text-gray-600 dark:text-gray-300'}`}>Harder</button>
                    </div>
                    <m.button onClick={fetchQuestion}
                      whileHover={{ scale: 1.01 }} whileTap={{ scale: 0.98 }}
                      className="w-full py-3 bg-gradient-to-r from-indigo-600 to-violet-600 text-white rounded-xl font-bold hover:from-indigo-700 hover:to-violet-700 transition shadow">
                      Next Question →
                    </m.button>
                  </m.div>
                )}
              </div>
            )}
          </div>
        </div>

        {/* accuracy sidebar */}
        <div className="bg-white dark:bg-gray-900 rounded-2xl border border-gray-100 dark:border-gray-800 p-5 shadow-sm h-fit">
          <h3 className="font-black text-gray-900 dark:text-white mb-4">Topic Accuracy</h3>
          <div className="space-y-3">
            {shownTopics.map(topic => {
              const acc = getAcc(topic)
              const s   = accuracyStats.subjects[topic]
              return (
                <div key={topic}>
                  <div className="flex items-center justify-between mb-1">
                    <span className="text-xs font-medium text-gray-600 dark:text-gray-400 flex items-center gap-1">
                      {ICONS[topic]} {SHORT[topic] || topicLabel(topic)}
                    </span>
                    <span className={`text-xs font-black ${acc === null ? 'text-gray-600' : acc >= 70 ? 'text-green-600 dark:text-green-400' : acc >= 40 ? 'text-amber-600 dark:text-amber-400' : 'text-rose-600 dark:text-rose-400'}`}>
                      {acc === null ? '—' : `${acc}%`}
                    </span>
                  </div>
                  <div className="h-1.5 bg-gray-100 dark:bg-gray-700 rounded-full overflow-hidden">
                    <m.div
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

          {/* No reset button: erasure is a parent-only, confirmed action. */}
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
              {/* `=== false`: the backend's push payload reports `available: null`. */}
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
                // Blank scores only on known-bad contact or no signal; the legacy
                // "poor" heuristic flags any focused student.
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

                    {/* Row 0b — link health, raw; absent fields (older bridge) show a dash. */}
                    <div className="border border-gray-700 rounded p-2 space-y-1">
                      <p className="text-gray-400 text-[10px] uppercase tracking-widest mb-1">Link</p>
                      <div className="flex flex-wrap gap-x-6 gap-y-1">
                        <span className={ing.preset_mismatch ? 'text-yellow-400' : 'text-gray-300'}>
                          Preset {ing.active_preset || '—'}
                          {ing.requested_preset && ing.requested_preset !== ing.active_preset
                            ? ` (asked ${ing.requested_preset})` : ''}
                          {ing.preset_mismatch ? ' — mismatch' : ''}
                        </span>
                        <span className={typeof ing.eeg_age_ms === 'number' && ing.eeg_age_ms > 2000 ? 'text-yellow-400' : 'text-gray-300'}>
                          EEG age {typeof ing.eeg_age_ms === 'number' ? `${ing.eeg_age_ms} ms` : '—'}
                        </span>
                        <span className="text-gray-300">
                          Last good {typeof ing.last_good_age_s === 'number' ? `${ing.last_good_age_s.toFixed(1)} s ago` : '—'}
                        </span>
                        <span className={ing.consecutive_errors > 0 ? 'text-yellow-400' : 'text-gray-300'}>
                          Sidecar errors {typeof ing.consecutive_errors === 'number' ? ing.consecutive_errors : '—'}
                        </span>
                        <span className={ing.reconnect_exhausted ? 'text-red-400' : ing.reconnecting ? 'text-yellow-400' : 'text-gray-300'}>
                          Auto-reconnect {ing.auto_reconnect === false ? 'off'
                            : ing.reconnect_exhausted ? `gave up after ${ing.reconnect_max_attempts}`
                            : ing.reconnecting ? `attempt ${ing.reconnect_attempt} of ${ing.reconnect_max_attempts}`
                            : ing.auto_reconnect === true ? 'on' : '—'}
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
                      {/* No difficulty tile: the backend chooses it, not the headband. */}
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
                        {/* Key `confidence` is a signal-quality score (0..100), not confidence in the scores. */}
                        <p className="text-gray-400 mb-1">Signal quality score <span className="text-white">{untrusted ? '—' : pct(feat.confidence)}</span></p>
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