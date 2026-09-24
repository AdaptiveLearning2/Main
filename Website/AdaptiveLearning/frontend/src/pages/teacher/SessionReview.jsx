import { Fragment, useEffect, useState } from 'react'
import { useParams, useLocation, Link } from 'react-router-dom'
import { motion } from 'framer-motion'
import { ArrowLeft, Brain, Camera, CheckCircle2, XCircle, Activity, ChevronDown } from 'lucide-react'
import {
  LineChart, Line, XAxis, YAxis,
  CartesianGrid, ReferenceLine, Legend, PieChart, Pie, Cell
} from 'recharts'
import ChartTooltip from '../../components/charts/ChartTooltip'
import AccessibleChart from '../../components/charts/AccessibleChart'
import SeriesFilter from '../../components/charts/SeriesFilter'
import { useSeriesFilter } from '../../hooks/useSeriesFilter'
import { asPercent, sliceSpec } from '../../components/charts/describeSeries'
import { apiFetch } from '../../lib/api'
import QuestionFigure from '../../components/questions/QuestionFigure'
import CCSSBadge from '../../components/questions/CCSSBadge'

// Fixed per FER+ label, so an emotion keeps its colour across sessions.
const EMOTION_COLOURS = {
  neutral: '#94a3b8', happy: '#10b981', surprise: '#38bdf8',
  sad: '#6366f1', angry: '#f43f5e', disgust: '#84cc16',
  fear: '#a855f7', contempt: '#f59e0b',
}

// calibrating/unknown are shown, not dropped, so categorisation isn't overstated.
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

/* ── the answers table ────────────────────────────────────────────────────
 * Readable text from a `session_answers` row with its embedded question. Each
 * tolerates `questions: null` (a question deleted from the bank).
 */

function topicLabel(q) {
  return q?.subject || 'Unknown topic'
}

/** `options` (unschema'd jsonb) as strings: an array or object, else `[]`. */
function optionList(q) {
  const raw = q?.options
  if (Array.isArray(raw)) return raw.map(String)
  if (raw && typeof raw === 'object') return Object.values(raw).map(String)
  return []
}

/** What the student picked, by text; falls back to the index. */
function answerLabel(opts, index) {
  if (index === null || index === undefined) return '—'
  const opt = opts[index]
  return opt === undefined ? `Option ${index}` : opt
}

/** Index of the correct option, or -1.
 * `correct_answer` is text, matched by value (trimmed, case-insensitive), once
 * per question so a duplicate distractor isn't also marked. A bounds-checked
 * numeric fallback covers a row that stores an index.
 */
function correctIndex(q, opts) {
  const want = q?.correct_answer
  if (want === null || want === undefined) return -1
  const a = String(want).trim().toLowerCase()
  const byValue = opts.findIndex(o => String(o).trim().toLowerCase() === a)
  if (byValue !== -1) return byValue
  if (/^\d+$/.test(a)) {
    const n = Number(a)
    if (n >= 0 && n < opts.length) return n
  }
  return -1
}

// Per-section archived SVG. The emotion ribbon has no archived equivalent.
function ArchivedChart({ url, label }) {
  return (
    <figure className="mt-3">
      <img src={url} alt={label} loading="lazy"
           className="w-full max-w-2xl mx-auto rounded-xl border border-gray-100 dark:border-gray-800 bg-white" />
      <figcaption className="text-[11px] text-gray-600 text-center mt-2 dark:text-gray-400">
        Archived chart — rendered when the session closed
      </figcaption>
    </figure>
  )
}

/** Remount on a new session id, so no state (derived `loading`, `err`)
 * survives from the previous session, e.g. on A→B→A navigation.
 */
export default function SessionReview() {
  const { sessionId } = useParams()
  return <SessionReviewBody key={sessionId} sessionId={sessionId} />
}

function SessionReviewBody({ sessionId }) {
  // Falls back to Live, not history.back(): a pasted link has no history.
  const location = useLocation()
  const backTo = location.state?.from || '/teacher/live'
  const [data, setData] = useState(null)
  const [err, setErr]   = useState(null)
  // The one expanded answer.
  const [openAnswer, setOpenAnswer] = useState(null)
  const [loadedFor, setLoadedFor] = useState(null)
  const loading = loadedFor !== sessionId
  // Separate from `data`, so an archive failure can't blank the rest.
  const [archive, setArchive] = useState(null)
  const [archiveErr, setArchiveErr] = useState(false)
  // Above the early exits below, or it would be a conditional hook.
  const { hidden: hiddenSeries, toggle: toggleSeries,
          showAll: showAllSeries, shownOf } = useSeriesFilter()

  useEffect(() => {
    let killed = false
    // Ignores "Hide sensor data": that covers reporting surfaces, not this page.
    apiFetch(`/api/signals/session/${sessionId}`)
      .then(d => {
        if (killed) return
        setData(d)
        // Archive only when every channel is empty: that's what expired rows look like.
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
      <div className="p-8 text-center text-gray-500 dark:text-gray-400">
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

  // Heart merges into cognitive rows by nearest timestamp: Recharts wants one dataset.
  const heartByT = heart
    .map(h => ({ t: new Date(h.ts).getTime(), h }))
    .filter(x => Number.isFinite(x.t))
    .sort((a, b) => a.t - b.t)

  // Union of both channels' timestamps: heart can be consented without EEG.
  const series = Array.from(new Set([...cognitiveByT.keys(), ...heartByT.map(x => x.t)]))
    .sort((a, b) => a - b)
    .map(t => {
      const c = cognitiveByT.get(t)
      return {
        t,
        focus:      typeof c?.focus      === 'number' ? c.focus      : null,
        stress:     typeof c?.stress     === 'number' ? c.stress     : null,
      }
    })

  // Each heart reading lands on one row (the nearest), never copied across rows;
  // `dot` on the lines makes a single point visible.
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

  // Heart sensor changes are marked, not spliced into one trace.
  const failovers = []
  for (let i = 1; i < heartByT.length; i++) {
    if (heartByT[i].h.source && heartByT[i].h.source !== heartByT[i - 1].h.source) {
      failovers.push({ t: heartByT[i].t, source: heartByT[i].h.source })
    }
  }

  // Proportion, from raw samples rather than the ribbon's 10s buckets.
  const emotionSlices = Object.entries(
    face.reduce((acc, f) => {
      if (!f.emotion) return acc          // a rejected window is not a reading
      acc[f.emotion] = (acc[f.emotion] || 0) + 1
      return acc
    }, {}),
  ).map(([name, value]) => ({ name, value }))

  const stressSlices = Object.entries(
    heart.reduce((acc, h) => {
      // calibrating/unknown kept as slices, not dropped.
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

  // One list per series drives the lines, the sr-only columns and the toggles,
  // so a column always names a drawn series. Ratios are 0..1, hence `asPercent`.
  // Heart series are gated on `hasHeart`; no `engagement` (it is the focus index).
  const TIMELINE_SERIES = [
    { key: 'focus',  label: 'Focus',      unit: '%', scale: asPercent,
      colour: '#6366f1', axis: 'ratio', name: 'Focus',      dot: false },
    // "EEG stress": must never share a label with the heart stress pie.
    { key: 'stress', label: 'EEG stress', unit: '%', scale: asPercent,
      colour: '#f43f5e', axis: 'ratio', name: 'EEG stress', dot: false },
    ...(hasHeart ? [
      // `dot` on: heart readings are sparse, so an isolated point needs one.
      { key: 'heart_rate_bpm', label: 'Heart rate', unit: ' bpm',
        colour: '#a855f7', axis: 'abs', name: 'Heart rate (bpm)', dot: { r: 2 } },
      { key: 'rmssd_ms',       label: 'RMSSD',      unit: ' ms',
        colour: '#f59e0b', axis: 'abs', name: 'RMSSD (ms)',       dot: { r: 2 } },
    ] : []),
  ]

  const shownSeries = shownOf(TIMELINE_SERIES)

  const TIMELINE_COLUMNS = shownSeries.map(
    ({ key, label, unit, scale }) => ({ key, label, unit, scale }),
  )

  // Mount an axis only for shown series: Recharts throws on a line naming a missing axis.
  const axisShown = (axis) => shownSeries.some((s) => s.axis === axis)

  const hasChart = series.length >= 2

  // url | 'empty' (drew nothing) | 'unavailable' (object unreadable) |
  // 'unarchived' | 'failed' (request) | 'pending'. Failure never reads as empty.
  const archivedChart = (name) => {
    if (archiveErr) return 'failed'
    if (!archive) return 'pending'
    if (!archive.archived) return 'unarchived'
    if ((archive.unavailable || []).includes(name)) return 'unavailable'
    const url = (archive.charts || {})[name]
    return url || 'empty'
  }

  // One distinct sentence per non-URL state.
  const NO_CHART_COPY = {
    empty: 'Nothing was recorded on this channel.',
    unavailable: 'The archived chart for this session could not be loaded.',
    unarchived: 'No signal samples for this session.',
    failed: 'The archived charts could not be loaded — try again.',
    pending: 'Loading the archived charts…',
  }

  const isUrl = (v) => typeof v === 'string' && v.startsWith('http')

  // Across a section's charts, a fault outranks an absence.
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
            <div className="flex items-center gap-2 mb-1">{t.icon}<span className="text-[11px] uppercase tracking-wider text-gray-600 font-bold dark:text-gray-400">{t.label}</span></div>
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
            {/* Archived SVGs once per-sample rows expire; the archive keeps cognitive and heart apart. */}
            {isUrl(archivedChart('cognitive_timeline')) || isUrl(archivedChart('heart_rate')) ? (
              <>
                <p className="text-sm text-gray-500 dark:text-gray-400">
                  The per-sample rows for this session have expired. These are the
                  charts as they were when it closed.
                </p>
                {isUrl(archivedChart('cognitive_timeline')) && (
                  <ArchivedChart url={archivedChart('cognitive_timeline')} label="Cognitive timeline" />
                )}
                {isUrl(archivedChart('heart_rate')) && (
                  <ArchivedChart url={archivedChart('heart_rate')} label="Heart rate and HRV" />
                )}
                {/* Say so when only one chart could be drawn. */}
                {anyUnavailable(['cognitive_timeline', 'heart_rate']) && (
                  <p className="text-[11px] text-amber-600 dark:text-amber-500 mt-2">
                    One archived chart for this session could not be loaded.
                  </p>
                )}
              </>
            ) : (
              <>
                <div className="text-5xl mb-2">🧠</div>
                <p className="text-sm text-gray-600 dark:text-gray-400">
                  {noChartCopy(['cognitive_timeline', 'heart_rate'])}
                </p>
                {/* Not beside an archived chart: that session has already ended. */}
                <p className="text-[11px] text-gray-600 mt-1 dark:text-gray-400">Once a sensor starts streaming, it'll show up here.</p>
              </>
            )}
          </div>
        ) : (
          <>
          {/* Renders nothing below two series. */}
          <SeriesFilter series={TIMELINE_SERIES} hidden={hiddenSeries} onToggle={toggleSeries} />
          {shownSeries.length === 0 ? (
            /* Every series hidden: say so, offer Show all, render no chart. */
            <div className="h-72 flex flex-col items-center justify-center gap-3 rounded-xl bg-slate-50 dark:bg-gray-800">
              <p className="text-sm text-gray-600 dark:text-gray-400">
                No measurements selected.
              </p>
              <button type="button" onClick={showAllSeries}
                      className="px-3 py-2.5 min-h-[44px] rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-900 text-xs font-bold text-gray-900 dark:text-white hover:bg-slate-50 dark:hover:bg-gray-800 transition">
                Show all
              </button>
            </div>
          ) : (
          <AccessibleChart className="h-72"
            headline={`Session replay over ${series.length} readings.`}
            rows={series} rowKey="t" rowLabel="Seconds in"
            columns={TIMELINE_COLUMNS}>
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
                {/* bpm/RMSSD get their own axis; each mounts only while a shown series uses it. */}
                {axisShown('ratio') && <YAxis yAxisId="ratio" domain={[0, 1]} fontSize={10} />}
                {axisShown('abs') && (
                  <YAxis yAxisId="abs" orientation="right" domain={['auto', 'auto']}
                         fontSize={10} />
                )}
                <ChartTooltip
                  labelFormatter={(v) => fmtTime(v)}
                  formatter={(v) => (typeof v === 'number' ? v.toFixed(2) : v)}
                />
                <Legend wrapperStyle={{ fontSize: 12 }} />
                {shownSeries.map((s) => (
                  <Line key={s.key} yAxisId={s.axis} type="monotone" dataKey={s.key}
                        name={s.name} stroke={s.colour} dot={s.dot}
                        connectNulls isAnimationActive={false} />
                ))}

                {/* Heart sensor changes; gated on the "abs" axis they reference, not hasHeart. */}
                {axisShown('abs') && failovers.map((f, i) => (
                  <ReferenceLine key={`fo-${i}`} yAxisId="abs" x={f.t}
                                 stroke="#a855f7" strokeOpacity={0.5} />
                ))}

                {/* Answer markers as vertical reference lines. */}
                {axisShown('ratio') && answers.map((a, i) => {
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
          </AccessibleChart>
          )}
          </>
        )}
        {/* Same gate as the markers (the ratio axis), not `shownSeries.length`. */}
        {hasChart && axisShown('ratio') && answers.length > 0 && (
          <p className="text-[11px] text-gray-600 mt-2 dark:text-gray-400">
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
            {/* No archived ribbon: the archive kept only the pie. Compare states, not strings. */}
            <p className="text-sm text-gray-600 dark:text-gray-400">
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
                <span className="text-[9px] text-gray-600 mt-0.5 dark:text-gray-400">{fmtTime(r.t)}</span>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Proportion beside the ribbon's sequence; 'unavailable' still mounts, to report it. */}
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
                  : <p className="text-sm text-gray-600 py-6 text-center dark:text-gray-400">{NO_CHART_COPY.unavailable}</p>
              ) : (
              <AccessibleChart className="h-52"
                {...sliceSpec('Emotion mix', emotionSlices, 'samples', { rowLabel: 'Emotion' })}>
                  <PieChart>
                    <Pie data={emotionSlices} dataKey="value" nameKey="name"
                         innerRadius="45%" outerRadius="75%" paddingAngle={2}>
                      {emotionSlices.map(sl => (
                        <Cell key={sl.name} fill={EMOTION_COLOURS[sl.name] || '#94a3b8'} />
                      ))}
                    </Pie>
                    <ChartTooltip formatter={(v, n) => [`${v} samples`, n]} />
                    <Legend wrapperStyle={{ fontSize: 11 }} />
                  </PieChart>
              </AccessibleChart>
              )}
            </div>
          )}
          {(stressSlices.length > 0 || isUrl(archivedChart('stress_pie'))
            || archivedChart('stress_pie') === 'unavailable') && (
            <div className="bg-white dark:bg-gray-900 rounded-2xl border border-gray-100 dark:border-gray-800 shadow-sm p-5">
              {/* Not bare "Stress": never share a label with "EEG stress". */}
              <h2 className="font-black text-gray-900 dark:text-white mb-3 text-sm">Heart-rate stress</h2>
              {stressSlices.length === 0 ? (
                isUrl(archivedChart('stress_pie'))
                  ? <ArchivedChart url={archivedChart('stress_pie')} label="Autonomic arousal" />
                  : <p className="text-sm text-gray-600 py-6 text-center dark:text-gray-400">{NO_CHART_COPY.unavailable}</p>
              ) : (
              <AccessibleChart className="h-52"
                {...sliceSpec('Heart-rate stress', stressSlices, 'windows', { rowLabel: 'Band' })}>
                  <PieChart>
                    <Pie data={stressSlices} dataKey="value" nameKey="name"
                         innerRadius="45%" outerRadius="75%" paddingAngle={2}>
                      {stressSlices.map(sl => (
                        <Cell key={sl.name} fill={STRESS_COLOURS[sl.name] || '#94a3b8'} />
                      ))}
                    </Pie>
                    <ChartTooltip formatter={(v, n) => [`${v} windows`, n]} />
                    <Legend wrapperStyle={{ fontSize: 11 }} />
                  </PieChart>
              </AccessibleChart>
              )}
            </div>
          )}
        </div>
      )}

      <div className="bg-white dark:bg-gray-900 rounded-2xl border border-gray-100 dark:border-gray-800 shadow-sm overflow-hidden">
        <h2 className="font-black text-gray-900 dark:text-white px-5 pt-5 mb-3">Answers</h2>
        {answers.length === 0 ? (
          <p className="px-5 pb-5 text-sm text-gray-600 dark:text-gray-400">No answers recorded.</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="text-left text-[11px] uppercase tracking-wider text-gray-600 dark:text-gray-400">
                <tr>
                  <th className="px-5 py-2 font-bold">Time</th>
                  <th className="font-bold">Topic</th>
                  <th className="font-bold">Answered</th>
                  <th className="font-bold pr-5">Result</th>
                </tr>
              </thead>
              <tbody>
                {answers.map((a, i) => {
                  const t = new Date(a.answered_at).getTime()
                  const q = a.questions || null
                  const opts = optionList(q)
                  // Once per question, so a repeated option can't mark two rows correct.
                  const correctIdx = correctIndex(q, opts)
                  const open = openAnswer === i
                  return (
                    <Fragment key={i}>
                      <tr className="border-t border-gray-50 dark:border-gray-800">
                        <td className="px-5 py-2 text-gray-500 whitespace-nowrap dark:text-gray-400">{fmtTime(t)}</td>
                        <td className="text-gray-700 dark:text-gray-300">
                          <button
                            type="button"
                            onClick={() => setOpenAnswer(open ? null : i)}
                            aria-expanded={open}
                            title={q?.question_text || 'The question is no longer in the bank'}
                            className="flex items-center gap-1 font-bold text-violet-700 dark:text-violet-300 hover:underline">
                            {topicLabel(q)}
                            <ChevronDown size={12} aria-hidden="true"
                              className={`transition ${open ? 'rotate-180' : ''}`} />
                          </button>
                        </td>
                        <td className="text-gray-700 dark:text-gray-300">
                          {answerLabel(opts, a.selected_index)}
                        </td>
                        <td className="pr-5">
                          {a.correct
                            ? <span className="text-emerald-500 flex items-center gap-1"><CheckCircle2 size={14} /> correct</span>
                            : <span className="text-rose-500 flex items-center gap-1"><XCircle size={14} /> wrong</span>}
                        </td>
                      </tr>
                      {open && (
                        <tr className="border-t border-gray-50 dark:border-gray-800 bg-slate-50 dark:bg-gray-800/50">
                          <td colSpan={4} className="px-5 py-3">
                            {q ? (
                              <>
                                <p className="text-sm font-bold text-gray-900 dark:text-white">
                                  {q.question_text}
                                </p>
                                {/* Without the figure it is a different question. */}
                                <QuestionFigure figure={q.figure} />
                                <CCSSBadge standard={q.ccss_standard} />
                                {q.difficulty && (
                                  <p className="mt-0.5 text-xs text-gray-600 dark:text-gray-400">
                                    Difficulty: {q.difficulty}
                                  </p>
                                )}
                                {opts.length > 0 ? (
                                  <ul className="mt-2 space-y-1">
                                    {opts.map((opt, oi) => {
                                      const picked = oi === a.selected_index
                                      const right = oi === correctIdx
                                      return (
                                        <li key={oi}
                                          className={`text-sm flex items-start gap-2 ${
                                            right ? 'text-emerald-700 dark:text-emerald-300 font-bold'
                                              : picked ? 'text-rose-700 dark:text-rose-300 font-bold'
                                                : 'text-gray-700 dark:text-gray-300'}`}>
                                          <span aria-hidden="true">{right ? '✓' : picked ? '✗' : '·'}</span>
                                          <span>
                                            {opt}
                                            {/* In words, not only colour and glyph. */}
                                            {picked && <span className="ml-1 text-xs font-normal">(chosen)</span>}
                                            {right && <span className="ml-1 text-xs font-normal">(correct answer)</span>}
                                          </span>
                                        </li>
                                      )
                                    })}
                                  </ul>
                                ) : (
                                  <p className="mt-2 text-sm text-gray-600 dark:text-gray-400">
                                    The options for this question were not recorded.
                                  </p>
                                )}
                              </>
                            ) : (
                              // The answer happened; only the question is gone.
                              <p className="text-sm text-gray-600 dark:text-gray-400">
                                This question is no longer in the question bank, so
                                its text and options cannot be shown.
                              </p>
                            )}
                          </td>
                        </tr>
                      )}
                    </Fragment>
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