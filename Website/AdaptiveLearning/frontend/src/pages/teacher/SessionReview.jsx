import { useEffect, useState } from 'react'
import { useParams, useLocation, Link } from 'react-router-dom'
import { motion } from 'framer-motion'
import { ArrowLeft, Brain, Camera, CheckCircle2, XCircle, Activity } from 'lucide-react'
import {
  LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer,
  CartesianGrid, ReferenceLine, Legend, PieChart, Pie, Cell
} from 'recharts'
import { apiFetch } from '../../lib/api'

// Fixed per label so the same emotion is always the same colour across sessions.
// Keyed on the FER+ labels the backend actually stores.
const EMOTION_COLOURS = {
  neutral: '#94a3b8', happy: '#10b981', surprise: '#38bdf8',
  sad: '#6366f1', angry: '#f43f5e', disgust: '#84cc16',
  fear: '#a855f7', contempt: '#f59e0b',
}

// calibrating/unknown are shown, not dropped: omitting them would overstate how much was categorised.
const STRESS_COLOURS = {
  low: '#10b981', moderate: '#f59e0b', high: '#f43f5e',
  calibrating: '#cbd5e1', unknown: '#94a3b8',
}

const EMOJI = { happy: '😀', neutral: '😐', confused: '😕', frustrated: '😤', sad: '😢', surprised: '😮', angry: '😠' }

function fmtTime(ms) {
  if (!Number.isFinite(ms)) return ''
  const d = new Date(ms)
  return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })
}

// Per-section label for the archived SVG, so a fallback never appears under
// the wrong heading. The emotion ribbon has no archived equivalent (it shows
// sequence; the archive only kept the pie), so it deliberately maps to nothing.
function ArchivedChart({ url, label }) {
  return (
    <figure className="mt-3">
      <img src={url} alt={label} loading="lazy"
           className="w-full max-w-2xl mx-auto rounded-xl border border-gray-100 dark:border-gray-800 bg-white" />
      <figcaption className="text-[11px] text-gray-400 text-center mt-2">
        Archived chart — rendered when the session closed
      </figcaption>
    </figure>
  )
}

/** Remount on a new session id, so nothing from the previous one survives.
 *
 * Two bugs came out of state outliving the session it described, and both are
 * navigation-ordinary rather than exotic:
 *
 * - **A→B→A showed A's first-visit data with no skeleton.** `loading` is derived
 *   as `loadedFor !== sessionId`, and B's request is cancelled on the way out
 *   without ever advancing `loadedFor` -- so coming back to A found it still
 *   reading `'A'`, called the page loaded, and drew stale rows while a fresh
 *   fetch was in flight.
 * - **An error on A masked a B that loaded fine.** `err` was never cleared, and
 *   the `if (err)` branch sits below `if (loading)`, so once B resolved the page
 *   fell straight through to A's error message.
 *
 * A key is the fix rather than clearing four pieces of state on the way in:
 * that would be one more thing to remember for the next one added, and it is
 * exactly the `set-state-in-effect` shape this component was refactored to
 * remove. `ChildDetail.jsx` already does this for the same reason.
 */
export default function SessionReview() {
  const { sessionId } = useParams()
  return <SessionReviewBody key={sessionId} sessionId={sessionId} />
}

function SessionReviewBody({ sessionId }) {
  // Both back links used to be hardcoded to /teacher/live, so opening a session
  // from history sent a teacher to Live Monitoring instead of back to the list.
  // Falls back to Live rather than history.back(): a teacher who arrived via a
  // pasted link has no history to return to.
  const location = useLocation()
  const backTo = location.state?.from || '/teacher/live'
  const [data, setData] = useState(null)
  const [err, setErr]   = useState(null)
  // Deriving loading from comparing loadedFor to the URL's session id means
  // switching sessions raises the skeleton immediately, not one render later.
  const [loadedFor, setLoadedFor] = useState(null)
  const loading = loadedFor !== sessionId
  // Kept separate from `data`: a failure loading the archive must not blank
  // the answers/tiles/accuracy above it, which don't depend on it.
  const [archive, setArchive] = useState(null)
  const [archiveErr, setArchiveErr] = useState(false)

  useEffect(() => {
    let killed = false
    // Deliberately does NOT honour the "Hide sensor data" switch in
    // lib/viewPrefs.js -- that control covers reporting surfaces, not this page.
    apiFetch(`/api/signals/session/${sessionId}`)
      .then(d => {
        if (killed) return
        setData(d)
        // No archive reset needed here: the component remounts per session id,
        // so there's no previous session's archive left to clear.
        // Only fetch the archive when every channel is empty -- that's what
        // expired per-sample rows look like, not one sensor being off.
        const empty = ['cognitive', 'face', 'heart']
          .every(k => !Array.isArray(d?.[k]) || d[k].length === 0)
        if (!empty) return
        return apiFetch(`/api/signals/session/${sessionId}/charts`)
          .then(a => { if (!killed) setArchive(a) })
          .catch(() => { if (!killed) setArchiveErr(true) })
      })
      .catch(e => { if (!killed) setErr(e.message || String(e)) })
      .finally(() => { if (!killed) setLoadedFor(sessionId) })
    return () => { killed = true }
  }, [sessionId])

  if (loading) {
    return (
      <div className="p-8 text-center text-gray-500">
        <motion.div animate={{ rotate: 360 }} transition={{ duration: 1, repeat: Infinity, ease: 'linear' }}
          className="w-10 h-10 border-4 border-violet-600 border-t-transparent rounded-full mx-auto mb-3" />
        Loading session…
      </div>
    )
  }

  if (err) {
    return (
      <div className="p-8">
        <Link to={backTo} className="text-sm text-violet-600 font-bold flex items-center gap-1 mb-4">
          <ArrowLeft size={14} /> Back
        </Link>
        <div className="bg-rose-50 dark:bg-rose-900/20 border border-rose-200 dark:border-rose-800 rounded-2xl p-6">
          <p className="font-black text-rose-700 dark:text-rose-300 mb-1">Could not load session</p>
          <p className="text-sm text-rose-600 dark:text-rose-400 break-all">{err}</p>
        </div>
      </div>
    )
  }

  const cognitive = Array.isArray(data?.cognitive) ? data.cognitive : []
  const face      = Array.isArray(data?.face)      ? data.face      : []
  const heart     = Array.isArray(data?.heart)     ? data.heart     : []
  const answers   = Array.isArray(data?.answers)   ? data.answers   : []

  // Numeric ms x-axis, more stable than category strings.
  const cognitiveByT = new Map(
    cognitive
      .map(c => {
        const t = new Date(c.ts).getTime()
        return Number.isFinite(t) ? [t, c] : null
      })
      .filter(Boolean),
  )

  // Heart is merged into the cognitive rows by nearest timestamp, since
  // Recharts wants one dataset and the two channels sample at different rates.
  const heartByT = heart
    .map(h => ({ t: new Date(h.ts).getTime(), h }))
    .filter(x => Number.isFinite(x.t))
    .sort((a, b) => a.t - b.t)

  // Union of both channels' timestamps, not cognitive alone: a student can
  // consent to heart without EEG, so cognitive can be empty while heart isn't.
  const series = Array.from(new Set([...cognitiveByT.keys(), ...heartByT.map(x => x.t)]))
    .sort((a, b) => a - b)
    .map(t => {
      const c = cognitiveByT.get(t)
      return {
        t,
        focus:      typeof c?.focus      === 'number' ? c.focus      : null,
        engagement: typeof c?.engagement === 'number' ? c.engagement : null,
        stress:     typeof c?.stress     === 'number' ? c.stress     : null,
      }
    })

  // Each heart reading lands on exactly one row (the nearest), not every row
  // within 15s -- cognitive arrives at 4Hz, so that would copy one reading
  // across up to 120 rows and draw it as a flat line. `dot` on the chart lines
  // below is what makes a single point visible.
  const rowIndexByT = series.map(r => r.t)
  let heartCollisions = 0
  for (const { t, h } of heartByT) {
    // Binary search for the insertion point, then compare both neighbours.
    let lo = 0, hiIdx = rowIndexByT.length
    while (lo < hiIdx) {
      const mid = (lo + hiIdx) >> 1
      if (rowIndexByT[mid] < t) lo = mid + 1
      else hiIdx = mid
    }
    const before = lo > 0 ? lo - 1 : null
    const after  = lo < series.length ? lo : null
    const dBefore = before !== null ? Math.abs(series[before].t - t) : Infinity
    const dAfter  = after  !== null ? Math.abs(series[after].t  - t) : Infinity
    const pick = dAfter < dBefore ? after : before
    // Bounded at 15s: a reading with no row that close belongs to a gap in cognitive recording.
    if (pick === null || Math.min(dBefore, dAfter) > 15_000) continue
    const row = series[pick]
    const d = Math.min(dBefore, dAfter)
    // Two readings (e.g. headband and camera at the same instant) can land on
    // one row. Closest wins, first wins a tie; the loser is counted, not merged or averaged.
    if (row.heart_rate_bpm !== undefined && row._heartDist <= d) {
      heartCollisions++
      continue
    }
    if (row.heart_rate_bpm !== undefined) heartCollisions++
    row._heartDist = d
    row.heart_rate_bpm = typeof h.heart_rate_bpm === 'number' ? h.heart_rate_bpm : null
    row.rmssd_ms = typeof h.rmssd_ms === 'number' ? h.rmssd_ms : null
  }
  if (heartCollisions) {
    console.warn(
      `[session-review] ${heartCollisions} heart reading(s) shared a plotted ` +
      'timestamp with another and are not drawn; the nearest one is shown.')
  }

  const hasHeart = series.some(r => r.heart_rate_bpm !== undefined && r.heart_rate_bpm !== null)

  // Marks where the heart sensor changed mid-session, rather than splicing
  // both into one continuous trace, which would look like a real physiological event.
  const failovers = []
  for (let i = 1; i < heartByT.length; i++) {
    if (heartByT[i].h.source && heartByT[i].h.source !== heartByT[i - 1].h.source) {
      failovers.push({ t: heartByT[i].t, source: heartByT[i].h.source })
    }
  }

  // Shows proportion, which the ribbon below can't (it shows sequence).
  // Uses raw samples, not the ribbon's 10s buckets, so a 10s bucket doesn't outweigh a 2s one.
  const emotionSlices = Object.entries(
    face.reduce((acc, f) => {
      if (!f.emotion) return acc          // a rejected window is not a reading
      acc[f.emotion] = (acc[f.emotion] || 0) + 1
      return acc
    }, {}),
  ).map(([name, value]) => ({ name, value }))

  const stressSlices = Object.entries(
    heart.reduce((acc, h) => {
      // calibrating/unknown kept as slices, not dropped, so the pie doesn't overstate categorisation.
      const key = h.stress_category || 'unknown'
      acc[key] = (acc[key] || 0) + 1
      return acc
    }, {}),
  ).map(([name, value]) => ({ name, value }))

  const tMin = series.length ? series[0].t : 0
  const tMax = series.length ? series[series.length - 1].t : 0

  // Emotion ribbon, bucketed every ~10s.
  const ribbon = []
  let lastBucket = 0
  face.forEach(f => {
    const t = new Date(f.ts).getTime()
    if (Number.isFinite(t) && t - lastBucket > 10_000) {
      ribbon.push({ t, emotion: f.emotion })
      lastBucket = t
    }
  })

  const totalAnswers   = answers.length
  const correctAnswers = answers.filter(a => a.correct).length
  const acc = totalAnswers ? Math.round((correctAnswers / totalAnswers) * 100) : 0

  const hasChart = series.length >= 2

  // Five distinct states, so a backend hiccup or a missing archive is never
  // reported as "nothing was recorded":
  //   url          -- archived and still readable
  //   'empty'      -- the archive ran and this channel drew nothing
  //   'unavailable'-- a path was recorded but the object couldn't be read
  //   'unarchived' -- the archive never ran for this session
  //   'failed'     -- the request for it failed
  //   'pending'    -- still waiting on the request
  const archivedChart = (name) => {
    if (archiveErr) return 'failed'
    if (!archive) return 'pending'
    if (!archive.archived) return 'unarchived'
    if ((archive.unavailable || []).includes(name)) return 'unavailable'
    const url = (archive.charts || {})[name]
    return url || 'empty'
  }

  // One distinct sentence per non-URL state, so a teacher can tell "nothing recorded" from "failed to load".
  const NO_CHART_COPY = {
    empty: 'Nothing was recorded on this channel.',
    unavailable: 'The archived chart for this session could not be loaded.',
    unarchived: 'No signal samples for this session.',
    failed: 'The archived charts could not be loaded — try again.',
    pending: 'Loading the archived charts…',
  }

  const isUrl = (v) => typeof v === 'string' && v.startsWith('http')

  // A section can cover multiple charts with independently differing states
  // (e.g. cognitive_timeline empty while heart_rate is unavailable), so a
  // fault always outranks a mere absence when reporting on the set.
  const anyUnavailable = (names) => names.some(n => archivedChart(n) === 'unavailable')

  const noChartCopy = (names) =>
    anyUnavailable(names)
      ? NO_CHART_COPY.unavailable
      : NO_CHART_COPY[archivedChart(names[0])]

  return (
    <div className="p-6 lg:p-8 pb-12 space-y-6">
      <Link to={backTo} className="text-sm text-violet-600 font-bold flex items-center gap-1 hover:text-violet-700">
        <ArrowLeft size={14} /> Back to live
      </Link>

      <motion.div initial={{ opacity: 0, y: -8 }} animate={{ opacity: 1, y: 0 }}>
        <h1 className="text-3xl font-black text-gray-900 dark:text-white">Session Review</h1>
        <p className="text-gray-500 dark:text-gray-400 mt-1 text-sm font-mono break-all">id: {sessionId}</p>
      </motion.div>

      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        {[
          { label: 'Cognitive samples', value: cognitive.length, icon: <Brain size={16} className="text-indigo-500" /> },
          { label: 'Face samples',      value: face.length,      icon: <Camera size={16} className="text-pink-500" /> },
          { label: 'Answers',           value: totalAnswers,     icon: <Activity size={16} className="text-emerald-500" /> },
          { label: 'Accuracy',          value: totalAnswers ? `${acc}%` : '—', icon: <CheckCircle2 size={16} className="text-violet-500" /> },
        ].map(t => (
          <div key={t.label} className="bg-white dark:bg-gray-900 rounded-2xl border border-gray-100 dark:border-gray-800 p-4 shadow-sm">
            <div className="flex items-center gap-2 mb-1">{t.icon}<span className="text-[11px] uppercase tracking-wider text-gray-400 font-bold">{t.label}</span></div>
            <div className="text-2xl font-black text-gray-900 dark:text-white">{t.value}</div>
          </div>
        ))}
      </div>

      <div className="bg-white dark:bg-gray-900 rounded-2xl border border-gray-100 dark:border-gray-800 shadow-sm p-5">
        <h2 className="font-black text-gray-900 dark:text-white mb-4 flex items-center gap-2">
          <Brain size={18} className="text-indigo-600" /> Cognitive timeline
        </h2>
        {!hasChart ? (
          <div className="text-center py-12">
            {/* Falls back to the archived SVGs once per-sample rows have expired. Both are shown since the
                archive keeps cognitive and heart separate, unlike the live merged trace. */}
            {isUrl(archivedChart('cognitive_timeline')) || isUrl(archivedChart('heart_rate')) ? (
              <>
                <p className="text-sm text-gray-500">
                  The per-sample rows for this session have expired. These are the
                  charts as they were when it closed.
                </p>
                {isUrl(archivedChart('cognitive_timeline')) && (
                  <ArchivedChart url={archivedChart('cognitive_timeline')} label="Cognitive timeline" />
                )}
                {isUrl(archivedChart('heart_rate')) && (
                  <ArchivedChart url={archivedChart('heart_rate')} label="Heart rate and HRV" />
                )}
                {/* One chart drawn, the other unreadable -- said explicitly so a partial view isn't mistaken for the whole session. */}
                {anyUnavailable(['cognitive_timeline', 'heart_rate']) && (
                  <p className="text-[11px] text-amber-600 dark:text-amber-500 mt-2">
                    One archived chart for this session could not be loaded.
                  </p>
                )}
              </>
            ) : (
              <>
                <div className="text-5xl mb-2">🧠</div>
                <p className="text-sm text-gray-400">
                  {noChartCopy(['cognitive_timeline', 'heart_rate'])}
                </p>
                {/* Only shown here, not beside an archived chart -- that would tell a teacher to wait on a session that already ended. */}
                <p className="text-[11px] text-gray-400 mt-1">Once a sensor starts streaming, it'll show up here.</p>
              </>
            )}
          </div>
        ) : (
          <div className="h-72">
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={series} margin={{ top: 5, right: 20, left: 0, bottom: 5 }}>
                <CartesianGrid strokeDasharray="3 3" opacity={0.3} />
                <XAxis
                  dataKey="t"
                  type="number"
                  domain={[tMin, tMax]}
                  scale="time"
                  tickFormatter={fmtTime}
                  fontSize={10}
                  minTickGap={50}
                />
                {/* Two axes with explicit ids: bpm/RMSSD don't belong on the 0-1 ratio scale. */}
                <YAxis yAxisId="ratio" domain={[0, 1]} fontSize={10} />
                {hasHeart && (
                  <YAxis yAxisId="abs" orientation="right" domain={['auto', 'auto']}
                         fontSize={10} />
                )}
                <Tooltip
                  labelFormatter={(v) => fmtTime(v)}
                  formatter={(v) => (typeof v === 'number' ? v.toFixed(2) : v)}
                />
                <Legend wrapperStyle={{ fontSize: 12 }} />
                <Line yAxisId="ratio" type="monotone" dataKey="focus"      stroke="#6366f1" dot={false} connectNulls isAnimationActive={false} />
                <Line yAxisId="ratio" type="monotone" dataKey="engagement" stroke="#10b981" dot={false} connectNulls isAnimationActive={false} />
                {/* "EEG stress" not bare "stress": distinct from the heart-derived stress_category pie below, must never share a label. */}
                <Line yAxisId="ratio" type="monotone" dataKey="stress" name="EEG stress" stroke="#f43f5e" dot={false} connectNulls isAnimationActive={false} />

                {/* `dot` on, unlike the cognitive lines: heart readings are sparse
                    (one held window per ~10s vs thousands of cognitive samples),
                    so an isolated point needs a dot to be visible at all. */}
                {hasHeart && (
                  <Line yAxisId="abs" type="monotone" dataKey="heart_rate_bpm" name="Heart rate (bpm)"
                        stroke="#a855f7" dot={{ r: 2 }} connectNulls isAnimationActive={false} />
                )}
                {hasHeart && (
                  <Line yAxisId="abs" type="monotone" dataKey="rmssd_ms" name="RMSSD (ms)"
                        stroke="#f59e0b" dot={{ r: 2 }} connectNulls isAnimationActive={false} />
                )}

                {/* Marks where the heart sensor changed. Gated on hasHeart (not
                    just failovers.length) since the "abs" axis these reference
                    only mounts when hasHeart is true. */}
                {hasHeart && failovers.map((f, i) => (
                  <ReferenceLine key={`fo-${i}`} yAxisId="abs" x={f.t}
                                 stroke="#a855f7" strokeOpacity={0.5} />
                ))}

                {/* Answer markers as vertical reference lines. */}
                {answers.map((a, i) => {
                  const x = new Date(a.answered_at).getTime()
                  if (!Number.isFinite(x) || x < tMin || x > tMax) return null
                  return (
                    <ReferenceLine
                      key={i}
                      yAxisId="ratio"
                      x={x}
                      stroke={a.correct ? '#10b981' : '#f43f5e'}
                      strokeDasharray="3 3"
                      strokeOpacity={0.7}
                    />
                  )
                })}
              </LineChart>
            </ResponsiveContainer>
          </div>
        )}
        {hasChart && answers.length > 0 && (
          <p className="text-[11px] text-gray-400 mt-2">
            Vertical lines = answer events · <span className="text-emerald-500">green</span> correct ·{' '}
            <span className="text-rose-500">red</span> incorrect
          </p>
        )}
      </div>

      <div className="bg-white dark:bg-gray-900 rounded-2xl border border-gray-100 dark:border-gray-800 shadow-sm p-5">
        <h2 className="font-black text-gray-900 dark:text-white mb-4 flex items-center gap-2">
          <Camera size={18} className="text-pink-600" /> Emotion timeline
        </h2>
        {ribbon.length === 0 ? (
          <div className="text-center py-8">
            <div className="text-4xl mb-2">📷</div>
            {/* No archived equivalent here, deliberately: the archive only kept the proportion pie (shown below), not the sequence this section shows. */}
            {/* Compared against the state, not the rendered string, since two states can share wording. */}
            <p className="text-sm text-gray-400">
              {isUrl(archivedChart('emotion_pie'))
                ? 'The per-sample rows have expired, so the moment-by-moment timeline is gone. The emotion mix is below.'
                : archivedChart('emotion_pie') === 'unavailable'
                  ? NO_CHART_COPY.unavailable
                  : archivedChart('emotion_pie') === 'empty'
                    ? 'Nothing was recorded on the camera channel.'
                    : 'No face samples for this session.'}
            </p>
          </div>
        ) : (
          <div className="flex gap-1 overflow-x-auto pb-2">
            {ribbon.map((r, i) => (
              <div key={i} title={`${fmtTime(r.t)} — ${r.emotion || 'unknown'}`}
                   className="flex flex-col items-center text-xs flex-shrink-0 w-14">
                <span className="text-2xl">{EMOJI[r.emotion] || '🙂'}</span>
                <span className="text-[9px] text-gray-400 mt-0.5">{fmtTime(r.t)}</span>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Shows proportion beside the ribbon's sequence: a single bad stretch
          and a whole bad session look the same scrolling through emojis.
          'unavailable' still mounts the section rather than hiding it, so an unreadable chart is reported, not silently dropped. */}
      {(emotionSlices.length > 0 || stressSlices.length > 0
        || isUrl(archivedChart('emotion_pie')) || isUrl(archivedChart('stress_pie'))
        || anyUnavailable(['emotion_pie', 'stress_pie'])) && (
        <div className="grid md:grid-cols-2 gap-4">
          {(emotionSlices.length > 0 || isUrl(archivedChart('emotion_pie'))
            || archivedChart('emotion_pie') === 'unavailable') && (
            <div className="bg-white dark:bg-gray-900 rounded-2xl border border-gray-100 dark:border-gray-800 shadow-sm p-5">
              <h2 className="font-black text-gray-900 dark:text-white mb-3 text-sm">Emotion mix</h2>
              {emotionSlices.length === 0 ? (
                isUrl(archivedChart('emotion_pie'))
                  ? <ArchivedChart url={archivedChart('emotion_pie')} label="Emotion mix" />
                  : <p className="text-sm text-gray-400 py-6 text-center">{NO_CHART_COPY.unavailable}</p>
              ) : (
              <div className="h-52">
                <ResponsiveContainer width="100%" height="100%">
                  <PieChart>
                    <Pie data={emotionSlices} dataKey="value" nameKey="name"
                         innerRadius="45%" outerRadius="75%" paddingAngle={2}>
                      {emotionSlices.map(sl => (
                        <Cell key={sl.name} fill={EMOTION_COLOURS[sl.name] || '#94a3b8'} />
                      ))}
                    </Pie>
                    <Tooltip formatter={(v, n) => [`${v} samples`, n]} />
                    <Legend wrapperStyle={{ fontSize: 11 }} />
                  </PieChart>
                </ResponsiveContainer>
              </div>
              )}
            </div>
          )}
          {(stressSlices.length > 0 || isUrl(archivedChart('stress_pie'))
            || archivedChart('stress_pie') === 'unavailable') && (
            <div className="bg-white dark:bg-gray-900 rounded-2xl border border-gray-100 dark:border-gray-800 shadow-sm p-5">
              {/* Not bare "Stress": distinct physiological measurement from the "EEG stress" line above; never share the label. */}
              <h2 className="font-black text-gray-900 dark:text-white mb-3 text-sm">Heart-rate stress</h2>
              {stressSlices.length === 0 ? (
                isUrl(archivedChart('stress_pie'))
                  ? <ArchivedChart url={archivedChart('stress_pie')} label="Autonomic arousal" />
                  : <p className="text-sm text-gray-400 py-6 text-center">{NO_CHART_COPY.unavailable}</p>
              ) : (
              <div className="h-52">
                <ResponsiveContainer width="100%" height="100%">
                  <PieChart>
                    <Pie data={stressSlices} dataKey="value" nameKey="name"
                         innerRadius="45%" outerRadius="75%" paddingAngle={2}>
                      {stressSlices.map(sl => (
                        <Cell key={sl.name} fill={STRESS_COLOURS[sl.name] || '#94a3b8'} />
                      ))}
                    </Pie>
                    <Tooltip formatter={(v, n) => [`${v} windows`, n]} />
                    <Legend wrapperStyle={{ fontSize: 11 }} />
                  </PieChart>
                </ResponsiveContainer>
              </div>
              )}
            </div>
          )}
        </div>
      )}

      <div className="bg-white dark:bg-gray-900 rounded-2xl border border-gray-100 dark:border-gray-800 shadow-sm overflow-hidden">
        <h2 className="font-black text-gray-900 dark:text-white px-5 pt-5 mb-3">Answers</h2>
        {answers.length === 0 ? (
          <p className="px-5 pb-5 text-sm text-gray-400">No answers recorded.</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="text-left text-[11px] uppercase tracking-wider text-gray-400">
                <tr>
                  <th className="px-5 py-2 font-bold">Time</th>
                  <th className="font-bold">Question ID</th>
                  <th className="font-bold">Pick</th>
                  <th className="font-bold pr-5">Result</th>
                </tr>
              </thead>
              <tbody>
                {answers.map((a, i) => {
                  const t = new Date(a.answered_at).getTime()
                  return (
                    <tr key={i} className="border-t border-gray-50 dark:border-gray-800">
                      <td className="px-5 py-2 text-gray-500 whitespace-nowrap">{fmtTime(t)}</td>
                      <td className="text-gray-700 dark:text-gray-300 font-mono text-xs">{(a.question_id || '').slice(0, 12)}…</td>
                      <td className="text-gray-700 dark:text-gray-300">{a.selected_index ?? '—'}</td>
                      <td className="pr-5">
                        {a.correct
                          ? <span className="text-emerald-500 flex items-center gap-1"><CheckCircle2 size={14} /> correct</span>
                          : <span className="text-rose-500 flex items-center gap-1"><XCircle size={14} /> wrong</span>}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  )
}