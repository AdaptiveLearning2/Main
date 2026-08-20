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

const EEG_DEBUG = import.meta.env.VITE_EEG_DEBUG === 'true'

// How long to wait before offering the session to the sidecar again. The
// student opening the lesson before starting the local app is the ordinary
// sequence, so this is a normal path rather than an error path -- long enough
// not to hammer a loopback port, short enough that a lesson does not get far
// before recording begins.
const PUSH_RETRY_MS = 5000

// Named for the sensor a student would recognise, not the table. "Cognitive"
// and "face" are our words; a student has a headband and a camera.
const CHANNEL_LABELS = [
  ['cognitive', 'Headband'],
  ['heart',     'Heart sensor'],
  ['face',      'Camera'],
]

const TOPICS = ['ordering','rationals','expressions','algebra','geometry','angle_relationships','mean','median','mode','probability']
const ICONS  = { ordering:'🔢', rationals:'➗', expressions:'📐', algebra:'🔣', geometry:'📏', angle_relationships:'📐', mean:'〰️', median:'📊', mode:'🔁', probability:'🎲' }
const SHORT  = { angle_relationships: 'Angle Rel.' }
const GRADES = ['1st Grade','2nd Grade','3rd Grade','4th Grade','5th Grade','6th Grade','7th Grade','8th Grade','Highschool','College']

/** What to put under a toast when the only detail is the thrown thing.
 *
 * The three hardware failures on this page each wrote this out, one of them
 * without the `?.` -- so how an error reaches a student depended on which
 * device had failed. One place to change if that should ever say something
 * other than the raw message.
 */
const errorDetail = (e) => e?.message || String(e)

const initSubjects = () => {
  const s = {}; TOPICS.forEach(t => { s[t] = { correct: 0, attempts: 0 } }); return s
}

/**
 * Ingestion runs in one of two modes, reported by the backend as
 * `ingest_mode` and surfaced here as `headband.pushMode`. Under pull (the
 * co-located deployment) the backend polls the sidecar itself, and hardware
 * control goes through `/api/eeg/*`. Under push (hosted backend, local
 * sidecar) the backend has no route to the student's laptop, so this page
 * talks to the sidecar directly on loopback via `lib/sidecar.js`, and
 * `/api/eeg/*` refuses with 409. Comments below note only the mode-specific
 * reason at each call site, not this whole distinction again.
 */
export default function Adaptive() {
  const { user } = useAuth()

  // mode: 'solo' (pick your own grade) | 'class' (use class grade)
  const [mode, setMode] = useState(() => localStorage.getItem('adaptive_mode') || 'solo')
  const [grade, setGrade] = useState('1st Grade')
  const [classes, setClasses] = useState([])
  const [classId, setClassId] = useState('')
  const [bias, setBias] = useState(0) // -1 easier, 0 auto, +1 harder

  // How long the student meant this sitting to be, from their profile.
  //
  // Advisory, and deliberately so: when the time is up the page asks between
  // questions rather than ending anything. A timer that closed the session
  // would land mid-question about as often as not, discarding an answer a child
  // was part way through giving -- and the setting is a plan, not a limit
  // anyone asked to have enforced.
  //
  // null until the profile lands, so nothing is timed against a guess.
  const [durationMin, setDurationMin]         = useState(null)
  const [sessionStartedAt, setSessionStartedAt] = useState(null)
  const [elapsedMin, setElapsedMin]           = useState(0)
  const [timeUpDismissed, setTimeUpDismissed] = useState(false)
  const [finishing, setFinishing]             = useState(false)

  // Topic accuracy comes from `user_math_performance`, not from this browser.
  //
  // It was `localStorage.accuracyStats_<uid>`, which made this the only panel in
  // the app whose numbers were not the database's. It disagreed with the
  // dashboard on the same screen, it started from zero on a school computer,
  // and nothing server-side could correct it -- a parent erasing a channel left
  // the figures standing in the child's browser.
  //
  // Shape kept identical so the render sites are unchanged; `accuracyState` is
  // what stops a failed read drawing as a student who has never practised.
  const [accuracyStats, setAccuracyStats] = useState(
    () => ({ total: { correct: 0, attempts: 0 }, subjects: initSubjects() }))
  const [accuracyState, setAccuracyState] = useState('loading')  // loading | ready | failed

  const loadAccuracy = useCallback(async () => {
    if (!user?.id) return
    // `GET /api/performance/student/{id}` -- the endpoint StudentProgressReport
    // already uses, rather than a second raw PostgREST query of the same table
    // written out here. Two readers of one table meant two places to change
    // when its shape moves, and the backend's access rule (`_verify_can_view_student`,
    // which admits the student themselves) stops being the only description of
    // who may read this the moment a page goes around it.
    let rows
    try {
      rows = await apiFetch(`/api/performance/student/${user.id}`)
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
  }, [user])

  // One answer moves one topic by one. The backend names the topic it actually
  // attributed the answer to -- derived from the question row, not from
  // anything this page decided -- so applying it here is not a local guess
  // about *what* changed, only about a +1 the database has already made
  // atomically.
  //
  // It replaces a full re-read of the student's performance table after every
  // single answer, in the app's hottest loop, immediately after the request
  // that had just told the backend what changed.
  const applyAttempt = useCallback((topic, wasCorrect) => {
    if (!topic) return                 // nothing was attributed; nothing moved
    // One place, so the tile and the running total cannot disagree about what
    // an attempt does to a tally.
    const bump = ({ correct, attempts }) => ({
      correct:  correct  + (wasCorrect ? 1 : 0),
      attempts: attempts + 1,
    })
    setAccuracyStats(prev => {
      const prior = prev.subjects[topic]
      // The total counts every attempt, even one this page has no tile for.
      // `TOPICS` is a hardcoded ten and `math_topics` is a table, so the two
      // agree only until someone adds a row -- and returning early on an
      // unrecognised topic dropped the attempt from Overall Accuracy as well as
      // from the tile. `loadAccuracy`, which this replaced, summed every row
      // the server returned whether or not it recognised the name, so the two
      // paths disagreed the moment the table grew.
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
    // `pushMode` deliberately absent until a health response lands: guessing
    // "not push" is what produced an outage message on first paint.
    available: false, connected: false, samples: 0, lastTs: null,
    phase: 'idle', // idle | starting | scanning | connecting | connected
    deviceName: null,
    // Charge remaining, or null for "no reading". Null covers three cases that
    // all mean the same thing to a reader -- nothing connected, a bridge that
    // predates the field, and a headband that has not sent a BATTERY packet
    // yet -- and none of them may render as 0%, which is a real charge.
    battery: null,
  })

  // Sidecar stations (device-keyed EEG registry, e.g. multiple headband rigs in the
  // same room) -- distinct from the BLE headband name list below (muse_devices):
  // a station is *which sidecar stream* to use, chosen before the BLE pairing flow
  // (scan/connect by headband name) even starts.
  const [stations, setStations]     = useState([])
  // The camera is a registered station like any other, but it is never a
  // headband, so it is tracked apart from `stations` -- which the picker
  // filters to headbands precisely so nobody is offered a camera to pair.
  const [camera, setCamera]         = useState(
    { id: null, running: false, busy: false })
  const [stationId, setStationId]   = useState(null)

  // What the local sidecar reports about its own delivery, under push. Null
  // until asked, so "not running here" and "asked and down" stay distinguishable.
  const [push, setPush]               = useState(null)
  // Which channels actually delivered a reading since the last status poll.
  // Actual capture, not consent: a consented channel whose sensor dropped out
  // must stop being listed, or the chip claims a recording that is not
  // happening -- which is worse than showing nothing.
  const [recording, setRecording]     = useState([])
  // Last poll's cumulative counts, so a *delta* can be taken. The counts
  // themselves only ever grow, so `> 0` would latch on and never clear.
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

  useEffect(() => { sessionIdRef.current = sessionId }, [sessionId])

  // Unmount cleanup for the two things that outlived this page.
  //
  // `phaseTimer` is the 30s safety net that puts the headband card back to
  // idle if a connection attempt neither finishes nor throws. It was cleared
  // at the *start* of the next attempt and inside the catch, so navigating
  // away mid-connect left it pending: it fired half a minute later against an
  // unmounted component.
  //
  // `window.AL_currentSessionId` is set when the recorder is created and was
  // never unset, so it named a finished session for the rest of the tab's
  // life -- and the next reader of it could not tell that from a live one.
  useEffect(() => () => {
    clearTimeout(phaseTimer.current)
    delete window.AL_currentSessionId
  }, [])

  // The clock starts when the session does, not when the page opened. A student
  // who leaves the tab sitting on the setup screen for an hour has not been
  // practising, and telling them their 15 minutes are up before the first
  // question would make the setting worse than not having it.
  useEffect(() => {
    if (!sessionId) {
      setSessionStartedAt(null)
      setElapsedMin(0)
      setTimeUpDismissed(false)
      return
    }
    setSessionStartedAt(prev => prev ?? Date.now())
  }, [sessionId])

  useEffect(() => {
    if (!sessionStartedAt || !durationMin) return
    // 20s. The banner appears within a third of a minute of the mark, which is
    // as precise as "you have been at this for 15 minutes" deserves to be, and
    // it is a state update on a page that is otherwise idle between questions.
    const tick = () => setElapsedMin((Date.now() - sessionStartedAt) / 60000)
    tick()
    const id = setInterval(tick, 20000)
    return () => clearInterval(id)
  }, [sessionStartedAt, durationMin])

  const timeUp = !!durationMin && !timeUpDismissed && elapsedMin >= durationMin

  // load profile default grade + classes
  useEffect(() => {
    apiFetch('/api/profile/me').then(p => {
      if (p?.grade_level) setGrade(p.grade_level)
      // The student's saved starting point for the Easier/Auto/Harder control
      // below, and how long they meant to practise for. `??`, not `||`: 0 is
      // the adaptive bias and a real choice, so `||` would silently reset a
      // student who had picked it -- indistinguishable from never having set it.
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
        // Runs from mount, before a session exists and before the status
        // effect below would set pushMode -- otherwise the page shows "EEG
        // service not reachable" on first paint even under push.
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
    // sidecar, which is always true under push -- gating on falsiness alone
    // would return here before the push branch below ever runs.
    if (!headband.available && !headband.pushMode) return
    let alive = true
    // Under push the backend can't reach the sidecar either, so ask it directly.
    const source = headband.pushMode
      ? sidecarDevices().then(list => ({ devices: list })).catch(() => ({ devices: [] }))
      : eegDevices()
    source.then(d => {
      if (!alive) return
      // Tracked before the headband filter below, or enabling the camera would
      // depend on the picker's rules -- two unrelated things.
      const face = (d?.devices || []).find(x => x.kind === 'face')
      setCamera(c => ({ ...c, id: face?.device_id || null,
                        running: !!face?.running }))
      // This control connects a *headband*, and EEG_DEVICES registers cameras in
      // the same registry (`default:muse@8765,camera:face@0` is the shipped
      // example), so an unfiltered list offers "camera" as something to connect a
      // headband to. It also breaks the single-device auto-select below: two
      // entries mean stationId stays null and the button sits disabled next to a
      // picker whose second option is wrong.
      //
      // Filtered by excluding `face` rather than allow-listing `muse`/`sim`: a
      // new camera kind appearing here would be visibly wrong and get reported,
      // whereas a new *headband* kind being silently dropped would take the
      // count back to one and auto-select something else in its place.
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
  // by the status poll, because the two failures are the same one seen at
  // different times: the sidecar not up yet, and the sidecar gone again.
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

  // Headband telemetry while one is connected: charge, and under push whether
  // the link is still up (the status poll below reads the backend, which can't
  // see this under push). Polls every 5s -- also push's only watcher of the
  // link, so it shouldn't lag the pull path's equivalent.
  useEffect(() => {
    if (!headband.connected || !stationId) return
    // Skips under pull once a session exists -- the status poll below already
    // reads the same battery field then. Still runs under pull before a
    // session starts, and always under push.
    if (!headband.pushMode && sessionId) return
    let killed = false
    const read = async () => {
      try {
        const st = headband.pushMode ? await museState(stationId)
                                     : (await eegStatus(stationId))?.muse
        if (killed) return
        const pct = st?.ingestion?.battery_percent
        // Under push, the only thing watching for a headband that dropped on
        // its own. `=== false`, not falsiness: an absent field means the
        // sidecar didn't report, not that the headband went away.
        const dropped = headband.pushMode && st?.ingestion?.muse_connected === false
        // `undefined` from a bridge without the field, `null` before the first
        // BATTERY packet, a number once one arrives. Only the last is a
        // reading. Written as a typeof check and not `pct || null`, which would
        // turn a genuinely flat battery into "no reading" -- erasing the one
        // value most worth showing.
        setHeadband(s => ({
          ...s,
          battery: typeof pct === 'number' ? pct : null,
          ...(dropped ? { connected: false, phase: 'idle', deviceName: null, battery: null } : {}),
        }))
      } catch {
        // A failed read is not a flat battery, and it is not a fresh one
        // either. Leave whatever was last measured; the badge disappears on
        // disconnect, which is the state that actually invalidates it.
      }
    }
    read()
    const id = setInterval(read, 5000)
    return () => { killed = true; clearInterval(id) }
  }, [headband.connected, headband.pushMode, stationId, sessionId])

  // Close the session the student is in.
  //
  // This page had no way to do that at all: only Practice.jsx ever called
  // `/end`, so an adaptive session stayed open until the stale sweep on the
  // student's *next* start closed it -- which is also when its rollup was
  // written and its charts archived, both stamped at the sweep rather than at
  // the sitting. A duration prompt with nothing to offer but "keep going" would
  // have been the same decoration this change is removing.
  //
  // Clearing `sessionId` is what stops delivery: the handover effect's cleanup
  // is keyed on it and takes the student's token back off the sidecar. The
  // hardware stays paired, because finishing a sitting is not unplugging.
  const finishSession = async () => {
    setFinishing(true)
    try {
      // The call and how a failure is reported live in lib/session.js, because
      // Practice.jsx makes the same one and its copy had already drifted into
      // swallowing the error. Resetting the page is this page's own business.
      await endSession(sessionIdRef.current)
    } finally {
      setSessionId(null)
      setSessionCount(0)
      setData(null)
      setPhase('idle')
      setFinishing(false)
      // Cleared with the rest of the session state. It is set when the
      // recorder is created and named a finished session until the tab closed,
      // which the next reader could not tell from a live one.
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
        ...(s.ingest_mode === 'push' ? {} : { connected: !!s.poller?.running }),
        samples:   s.poller?.samples || 0,
        lastTs:    s.poller?.last_ts || null,
        // Only under pull, to avoid re-reading the same field the telemetry
        // effect above already polls under push. `typeof` check, not `pct ||
        // null`: 0% is a real charge and must not read as no reading.
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

  // Hand the session and the student's token to the local sidecar, and take
  // them back when the session ends. Only under push -- under pull the
  // backend's poller is already the writer, and running both would double-write
  // `cognitive_signals`, which has no dedupe key.
  useEffect(() => {
    if (!sessionId || !headband.pushMode) return
    let killed = false

    // Retried, not attempted once: the sidecar coming up after this page is
    // the normal order of events, and a single failed attempt used to leave
    // nothing pushed for the rest of the session.
    let attempt = null
    const handOver = () => {
      // Onto the same chain the cleanup's stop goes on, so the two are ordered
      // rather than racing. Off the chain this would be sequencing only the
      // stop against nothing, which reads like a fix and is not one.
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

    // The student's backend token expires roughly hourly and a lesson can run
    // longer. The sidecar holds one token for the session, so without this the
    // pushes start 401ing partway through and the samples pile up in a bounded
    // queue until they are dropped. Re-handing the same session id replaces the
    // token in place and leaves the queue alone.
    const { data: sub } = supabase.auth.onAuthStateChange((event, session) => {
      if (event !== 'TOKEN_REFRESHED' || killed) return
      // Token comes from the callback argument -- calling `getSession()` here
      // deadlocks on supabase-js v2's auth lock, held while this dispatches.
      // Chained with start/stop so a refresh during teardown can't re-hand a
      // token to a sidecar being stopped.
      const token = session?.access_token
      if (!token) return
      pushHandoff.current = pushHandoff.current
        .catch(() => {})
        .then(() => (killed ? null : startPush(sessionId, token)))
        .catch(() => {})
    })

    // Effect cleanup does not run on a tab close or a hard refresh, which is
    // how most lessons actually end. Without this the sidecar keeps the token
    // and keeps recording for up to an hour after the student walked away.
    // `pagehide` rather than `beforeunload`: it fires for the bfcache case too,
    // and `beforeunload` is unreliable on mobile.
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

  // Delivery counts, for the panel and for the recording chip. Slower than the
  // 3 s status poll: this is a local request but it is only ever read by a
  // human glancing at it.
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
        // `enabled: false` means reachable but deliberately not pushing
        // (PUSH_ENABLED off). Reachability and running are separate claims --
        // merging a flat `reachable: true` would render that as a healthy
        // session and erase a failed handover's error.
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
    // Wait for the mode: guessing "not push" before the health poll lands
    // would send the first poll to a backend that answers `available: null`
    // under push, reading as an outage.
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

  // Old browser copies are cleared rather than left to rot. They are no longer
  // read, but a stale `accuracyStats_<uid>` sitting in storage is the next
  // reader's false lead -- and on a shared computer it is one child's figures
  // outliving their session for no purpose at all.
  useEffect(() => {
    if (!user?.id) return
    localStorage.removeItem(`accuracyStats_${user.id}`)
  }, [user])

  useEffect(() => { localStorage.setItem('adaptive_mode', mode) }, [mode])

  // Stop one device and then release the shared push client if -- and only
  // if -- nothing else is still streaming through it. The device list that
  // decision is made from is also what updates the camera's own state, so the
  // card cannot claim to be recording into a client that has been torn down.
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
    // Push only. The button is disabled otherwise, and this returns rather than
    // trusting that -- the two guards protect different things: the disabled
    // attribute is the explanation to the student, this is the correctness.
    if (!camera.id || !headband.pushMode || camera.busy) return
    setCamera(c => ({ ...c, busy: true }))
    try {
      if (camera.running) {
        // Via the same helper as the headband: the camera's off-path never
        // released the push client, so turning it off with no headband running
        // left the sidecar holding the student's access token.
        await endPushDevice(camera.id)
      } else {
        // No session and no handover here. The camera comes on; whether
        // anything is *stored* is decided by whether a lesson is running, and
        // the sidecar simply has no consumer attached until then -- so frames
        // are captured and dropped rather than queued against a session the
        // student never started.
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

  const toggleHeadband = async () => {
    if (!stationId) return
    // Guarded separately from the main try below: a failed getOrCreateSession
    // used to reject the whole handler before any phase was set, leaving a
    // dead-looking button with no alert.
    //
    // Push pairs against a device, not a session, so it doesn't need one here.
    // Pull still does -- its reservation is scoped by session_id, and a failed
    // pull pairing is discarded at close instead.
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
    // Use a local var, not the recorder state var directly: setRecorder()
    // below doesn't take effect until the next render, so a read of
    // `recorder` later in this same call would still see the pre-update
    // value (null, right after a disconnect) instead of what was just
    // created.
    let rec = recorder
    if (!rec) {
      rec = createSignalRecorder({ sessionId: activeSessionId, deviceId: stationId })
      setRecorder(rec)
      window.AL_currentSessionId = activeSessionId
    }

    // Under pull the backend proxies scan/connect via /api/eeg/muse/*; under
    // push those refuse (409) and the page talks to the sidecar on loopback
    // directly, admitted by `require_local_controller`.
    //
    // Defined here, not as a component-level memo, so the pull branch can
    // close over `rec`, created just above and deliberately not held in state.
    const hw = headband.pushMode ? {
      // Brings the hardware up and stops there. Delivery is started by the
      // `sessionId` effect below, which fires when `fetchQuestion` creates a
      // session -- so samples begin when the student begins, not when they
      // plug something in.
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
      begin:      () => rec.start(),
      disconnect: () => apiFetch('/api/eeg/muse/disconnect',
                                 { method: 'POST', body: { device_id: stationId } }),
      scan:       (sid) => apiFetch('/api/eeg/muse/refresh',
                                    { method: 'POST', body: { device_id: stationId, session_id: sid } }),
      connect:    (name, sid) => apiFetch('/api/eeg/muse/connect',
                                          { method: 'POST', body: { name, device_id: stationId, session_id: sid } }),
      status:     async () => (await eegStatus(stationId))?.muse || {},
      end:        () => rec.stop(),
    }

    // — Disconnect —
    if (headband.connected) {
      clearTimeout(phaseTimer.current)
      await hw.end()
      // Drop rather than reuse: it closed over deviceId at creation, so
      // reusing it after picking a different station would misattribute data.
      setRecorder(null)
      delete window.AL_currentSessionId
      // Clear battery too -- it describes the headband that just left, and
      // is the one number here a student acts on.
      setHeadband(s => ({ ...s, connected: false, phase: 'idle', deviceName: null, battery: null }))
      return
    }

    clearTimeout(phaseTimer.current)
    phaseTimer.current = setTimeout(() => {
      setHeadband(s => s.phase !== 'idle' && s.phase !== 'connected'
        ? { ...s, phase: 'idle', deviceName: null }
        : s)
    }, 30000)

    try {
      setHeadband(s => ({ ...s, phase: 'starting' }))
      const res = await hw.begin(activeSessionId)
      if (!res?.ok && !res?.running) throw new Error(res?.error || 'Could not start EEG session')

      // Disconnect any previous session first, or the headband is left in a
      // streaming state that throws BadStateError on the next connect.
      await hw.disconnect().catch(() => {})
      await new Promise(r => setTimeout(r, 1500))

      setHeadband(s => ({ ...s, phase: 'scanning' }))
      // session_id scopes the station reservation this scan claims, so closing
      // a different session of the same student can't release it.
      await hw.scan(activeSessionId)

      let devices = []
      let bluetoothEnabled = true
      for (let i = 0; i < 12; i++) {
        await new Promise(r => setTimeout(r, 1000))
        const st = await hw.status()
        devices = st?.ingestion?.muse_devices || []
        if (devices.length > 0) break
        // Bridge reports the PC's Bluetooth radio state directly (see
        // MuseBridgeService::bluetooth_enabled) — stop waiting immediately
        // instead of burning the full 12 s when the radio itself is off.
        if (st?.ingestion?.bluetooth_enabled === false) {
          bluetoothEnabled = false
          break
        }
      }

      if (devices.length === 0) {
        setHeadband(s => ({ ...s, phase: 'idle' }))
        // A longer dwell than the default, and the `Toaster` in App.jsx has a
        // close button: this one is instructions a student has to act on, not
        // a notification they only have to read.
        toast.error(
          bluetoothEnabled === false
            ? 'Bluetooth is turned off on this PC.'
            : 'No headband found.',
          {
            description: bluetoothEnabled === false
              ? 'Turn Bluetooth on in Windows Settings, then click Connect Headband again.'
              : 'Check the headband is switched on and within a metre of the computer, and that Bluetooth is enabled.',
            duration: 12_000,
          })
        return
      }

      const target = devices[0]
      setHeadband(s => ({ ...s, phase: 'connecting', deviceName: target }))
      await hw.connect(target, activeSessionId)

      // Bridge connects asynchronously; poll for it. A BadStateError here means
      // the headband is still streaming from a prior session and needs a power-cycle.
      let muse_ok = false
      for (let i = 0; i < 10; i++) {
        await new Promise(r => setTimeout(r, 1000))
        const st = await hw.status()
        if (st?.ingestion?.muse_connected) { muse_ok = true; break }
      }

      if (!muse_ok) {
        setHeadband(s => ({ ...s, phase: 'idle', deviceName: null }))
        toast.error('The headband was found but would not connect.', {
          description: 'Its firmware is still streaming from a previous session. '
            + 'Hold the power button until it switches off (descending beeps), wait ten '
            + 'seconds, switch it back on, then click Connect Headband again.',
          duration: 15_000,
        })
        return
      }

      setHeadband(s => ({ ...s, connected: true, phase: 'connected' }))

    } catch (e) {
      console.error('[headband]', e)
      clearTimeout(phaseTimer.current)
      setHeadband(s => ({ ...s, phase: 'idle', deviceName: null }))
      toast.error('The headband could not connect.', {
        description: errorDetail(e),
      })
    }
  }

  // `sendAccuracyToBackend` used to run here, pushing this browser's tallies
  // into `user_math_performance` before every question. It is gone, and it had
  // to go rather than merely stop being called: the backend now owns that table
  // and derives the topic from the question row, so a client upsert would
  // overwrite real counts with whatever one browser happened to remember --
  // and `Number(vals.correct) || null` turned every genuine zero into a null on
  // the way, which is why the table sat empty while the panel showed figures.
  const fetchQuestion = async () => {
    setPhase('loading'); setError(false)
    try {
      const activeSessionId = await getOrCreateSession()

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
    setSessionCount(n => n + 1)
    setPhase('result')

    // The answer goes to the backend. This page never did that -- it had no
    // `/api/sessions/{id}/answer` call at all -- so every question answered
    // here was counted in this browser and nowhere else, and `session_answers`,
    // `user_stats` and every report built on them stayed at zero however long a
    // student practised. `data.id` is the stored question's id, which the
    // generator now returns.
    // The POST, the missing-id guard and the failure toast all live in
    // `lib/session.js` now: `Practice.jsx` makes the same call against the same
    // endpoint and had drifted into having no `catch` at all, which is the
    // second time these two pages have disagreed about whether a failure is
    // worth mentioning -- `endSession` was extracted from the same pair for the
    // same reason. Correctness stays here, because the two pages hold questions
    // in different shapes and compare them differently.
    const res = await recordAnswer({
      sessionId: sessionIdRef.current,
      questionId: data?.id,
      selectedIndex: selectedAnswer,
      correct: isCorrect,
    })
    if (res) {
      // The response names the topic the backend attributed this to, so there
      // is nothing left to go and ask. A local *guess* at the topic is what had
      // to be avoided -- disagreeing copies of this number is the whole bug
      // that moved these figures out of localStorage -- and this is not one:
      // the name comes from the question row, the same place the write did.
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

  // What this headband has actually delivered.
  //
  // `headband.samples` is the *backend poller's* count, and under push there is
  // no backend poller -- so the card read "0 samples sent" for a whole session
  // while the sidecar's own status said 178 cognitive and 134 face rows
  // delivered and the database agreed with the sidecar. Third instance of the
  // same mistake on this page, after `available` and `connected`: a backend
  // field standing in for something the backend cannot see in this deployment.
  //
  // cognitive + heart, because both come off the headband and this is the
  // headband's card; the camera has its own. Read from the same `push.recorded`
  // the RECORDING chip is derived from, so the badge and the number cannot
  // disagree about whether anything is being written.
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
            {!headband.available && !headband.pushMode && <span className="text-[10px] font-bold px-2 py-0.5 bg-gray-100 dark:bg-gray-800 text-gray-500 rounded-full">offline</span>}
            {headband.pushMode && <span className="text-[10px] font-bold px-2 py-0.5 bg-gray-100 dark:bg-gray-800 text-gray-500 rounded-full">on your device</span>}
            {/* Three states, not two: push === null (not asked yet) renders
                nothing, a known not-recording state (including reachable but
                running: false, e.g. a restarted sidecar) is amber, and only
                reachable-and-running claims RECORDING. */}
            {headband.pushMode && push && push.running !== true &&
             (push.reachable === false || push.enabled === false || push.running === false) && (
              <span className="text-[10px] font-bold px-2 py-0.5 bg-amber-100 dark:bg-amber-900/40 text-amber-700 dark:text-amber-300 rounded-full">not recording</span>
            )}
            {headband.pushMode && push?.reachable && push?.running && (
              <span className="text-[10px] font-bold px-2 py-0.5 bg-emerald-100 dark:bg-emerald-900/40 text-emerald-700 dark:text-emerald-300 rounded-full">● RECORDING</span>
            )}
          </p>
          <p className="text-[11px] text-gray-400 mt-0.5">
            {headband.phase === 'scanning'   && '🔍 Scanning for Muse headbands via Bluetooth...'}
            {headband.phase === 'connecting' && `🔗 Connecting to ${headband.deviceName || 'headband'}...`}
            {headband.phase === 'starting'   && 'Starting EEG session...'}
            {headband.phase === 'connected'  && `${headbandSamples} samples sent · teacher can see your focus & stress live`}
            {headband.phase === 'idle' && (
              headband.connected
                ? `${headbandSamples} samples sent · teacher can see your focus & stress live`
                : headband.pushMode
                  ? (push && push.enabled === false
                      ? 'The app on this computer is running but is not set up to record (PUSH_ENABLED is off). Nothing is being saved for this session.'
                    : push && push.reachable === false
                      ? 'The app on this computer is not running, so nothing is being recorded. Start it and this will change on its own.'
                      // Counted from what the backend said it *stored*, not from
                      // what was sent -- it drops samples for a sensor that was
                      // declined, so a sent count would read as a healthy
                      // session that recorded nothing.
                      : push?.recorded
                        ? `${Object.values(push.recorded).reduce((a, b) => a + b, 0)} readings recorded from this computer.`
                        : 'Turn on your Muse S headband, then click Connect. It pairs through the app on this computer.')
                  : headband.available
                  ? 'EEG service ready. Turn on your Muse S headband then click Connect.'
                  : 'EEG service not reachable on port 8001. Make sure the EEGResearch backend is running.'
            )}
          </p>
        </div>
        {/* Station picker: only shown when the sidecar has more than one registered
            *headband* (e.g. several rigs in the same room). Cameras are filtered
            out where `stations` is set, so a deployment with one headband and a
            camera auto-selects the headband and never sees this. */}
        {stations.length > 1 && !headband.connected && (
          // Its only label was the disabled placeholder option, which is the
          // selected *value* rather than a name for the field -- several
          // screen readers announce nothing for it.
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
        {/* Not gated on sessionId: toggleHeadband creates the session lazily.
            `available` is null (not false) under push, so gating on falsiness
            alone would disable this button in exactly the mode where the page
            is the only thing that can pair. `stationId` still gates, and under
            push it's only set once the sidecar has answered. */}
        {/* Charge, beside the button that acts on it. Rendered only when there
            is a reading -- no "--%" placeholder, because the gap between
            connecting and the first BATTERY packet is normal and a permanent
            empty slot would read as a broken sensor rather than as a headband
            that has not said yet. `!= null` catches undefined too, and lets 0
            through: a flat battery is exactly what this is for. */}
        {headband.connected && headband.battery != null && (
          // `aria-label` as well as `title`. A title tooltip is not reliably
          // announced by screen readers and never appears on touch, so the one
          // number a student is asked to act on had no accessible name at all.
          // Interpolated so it reads as a value rather than a bare percentage
          // next to an icon.
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
            headband.connected ? 'bg-rose-500 hover:bg-rose-600 text-white' : 'bg-indigo-600 hover:bg-indigo-700 text-white'
          }`}>
          { headband.phase === 'starting'   ? 'Starting...'
          : headband.phase === 'scanning'   ? 'Scanning...'
          : headband.phase === 'connecting' ? 'Connecting...'
          : headband.connected              ? 'Disconnect'
          :                                   'Connect Headband' }
        </button>
      </motion.div>

      {/* Rendered only when a camera is registered. Disabled (not hidden) under
          pull: face_signals has exactly one writer, the push endpoint, so a
          camera started under pull would capture and store nothing. */}
      {camera.id && (
        <motion.div initial={{ opacity: 0, y: -6 }} animate={{ opacity: 1, y: 0 }}
          className="mb-6 rounded-2xl bg-white dark:bg-gray-900 border border-gray-100 dark:border-gray-800 p-4 flex items-center gap-4 shadow-sm">
          <div className="w-11 h-11 rounded-xl bg-fuchsia-600 grid place-items-center text-white text-lg">📷</div>
          <div className="flex-1 min-w-0">
            {/* Three states: the camera being on and a lesson recording are
                different claims, so only camera.running && sessionId earns the
                red dot -- otherwise a finished session's still-on camera would
                either falsely say RECORDING or falsely say off. */}
            <p className="font-bold text-sm flex items-center gap-2">
              Camera
              {camera.running && sessionId
                ? <span className="text-[10px] font-bold px-2 py-0.5 bg-emerald-100 dark:bg-emerald-900/40 text-emerald-700 dark:text-emerald-300 rounded-full">● RECORDING</span>
                : camera.running
                  ? <span className="text-[10px] font-bold px-2 py-0.5 bg-amber-100 dark:bg-amber-900/40 text-amber-700 dark:text-amber-300 rounded-full">on, not recording</span>
                  : <span className="text-[10px] font-bold px-2 py-0.5 bg-gray-100 dark:bg-gray-800 text-gray-500 rounded-full">off</span>}
            </p>
            <p className="text-[11px] text-gray-400 mt-0.5">
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

      {/* Time's up, asked rather than enforced.
          A banner and not a modal: it must never sit on top of a question a
          student is part way through, and "keep going" has to be the cheapest
          thing in the room -- the setting is a plan, not a limit anyone asked
          to have imposed on them. Dismissing it does not clear the preference,
          only this sitting's reminder. */}
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

      <div className="grid lg:grid-cols-3 gap-6">
        <div className="lg:col-span-2 space-y-4">
          {sessionCount > 0 && (
            <div className="flex gap-3 flex-wrap items-center">
              {/* Renders nothing rather than an empty chip when nothing is
                  recording. Under pull the sidecar reports no counts (the
                  poller is the writer), so headband.connected is the source
                  there instead. */}
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

          {/* "Reset stats" is gone. It cleared a browser key, which was
              harmless while these numbers lived in that browser; against
              `user_math_performance` the same button would delete a student's
              academic record from a one-click control with no confirmation --
              and that record is what the adaptive engine reads to pick the next
              question. Erasure is a parent-only, confirmed action elsewhere in
              this product, and this number belongs on that side of the line. */}
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
              {/* The push branch fires only when the sidecar itself did not
                  answer (`=== false`), not on the mode alone -- the backend's
                  own push payload reports `available: null` ("not probed"),
                  which reading as falsy would misrender as an outage. */}
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
                      {/* No "Next Question" tile. The sidecar used to emit a
                          question_policy here and it drove nothing -- difficulty
                          is chosen in the backend from correctness, topic history
                          and grade. Showing a difficulty next to live EEG implied
                          the headband was picking questions, which is the reason
                          it was deleted rather than merged. */}
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