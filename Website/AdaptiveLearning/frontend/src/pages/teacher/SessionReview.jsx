import { Fragment, useEffect, useMemo, useState } from 'react'
import { useParams, useLocation, Link } from 'react-router-dom'
import { m } from 'framer-motion'
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
import { EMOTION_COLOURS, UNKNOWN_EMOTION_COLOUR, emotionEmoji } from '../../lib/emotions'
import QuestionFigure from '../../components/questions/QuestionFigure'
import CCSSBadge from '../../components/questions/CCSSBadge'
import { correctIndex, optionList } from '../../lib/answerKey'
import { fmtDate } from '../../lib/dates'
import { buildTimeline, downsample } from '../../lib/timeline'
import { MEASURES } from '../../lib/signalGlossary'

// About two points per horizontal pixel; an hour at 4 Hz is ~14,000 rows.
const CHART_MAX_ROWS = 1500

// calibrating/unknown are shown, not dropped, so categorisation isn't overstated.
const STRESS_COLOURS = {
  low: '#10b981', moderate: '#f59e0b', high: '#f43f5e',
  calibrating: '#cbd5e1', unknown: '#94a3b8',
}


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

/** What the student picked, by text; falls back to the index. */
function answerLabel(opts, index) {
  if (index === null || index === undefined) return '—'
  const opt = opts[index]
  return opt === undefined ? `Option ${index}` : opt
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
  // Derived once per payload, not on every render (opening an answer row re-renders the page).
  const timeline = useMemo(() => buildTimeline(data), [data])
  // The SVG only; the sentence and the sr-only table read the full series.
  const chartRows = useMemo(() => downsample(timeline.series, CHART_MAX_ROWS), [timeline])
  const { hasHeart } = timeline
  // One list per series drives the lines, the sr-only columns and the toggles,
  // so a column always names a drawn series. Ratios are 0..1, hence `asPercent`.
  // Heart series are gated on `hasHeart`; no `engagement` (it is the focus index).
  const TIMELINE_SERIES = useMemo(() => [
    { key: 'focus',  label: MEASURES.focus.name, unit: '%', scale: asPercent,
      colour: '#6366f1', axis: 'ratio', name: 'Focus', dot: false },
    // Dashed, as on the reports: focus and calm are close for tritan vision.
    { key: 'calm',   label: MEASURES.calm.name,  unit: '%', scale: asPercent,
      colour: '#0d9488', axis: 'ratio', name: 'Calm',  dot: false, dash: '6 3' },
    ...(hasHeart ? [
      // `dot` on: heart readings are sparse, so an isolated point needs one.
      { key: 'heart_rate_bpm', label: MEASURES.heart_rate_bpm.name, unit: ' bpm',
        colour: '#a855f7', axis: 'abs', name: 'Heart rate (bpm)', dot: { r: 2 } },
      { key: 'rmssd_ms',       label: MEASURES.rmssd_ms.name,       unit: ' ms',
        colour: '#f59e0b', axis: 'abs', name: 'Heart-rate variability (ms)', dot: { r: 2 } },
    ] : []),
  ], [hasHeart])
  const shownSeries = useMemo(() => shownOf(TIMELINE_SERIES), [shownOf, TIMELINE_SERIES])
  // Stable, so AccessibleChart's memoised sentence is not rebuilt over every row per render.
  const TIMELINE_COLUMNS = useMemo(() => shownSeries.map(
    ({ key, label, unit, scale }) => ({ key, label, unit, scale }),
  ), [shownSeries])

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
        <m.div animate={{ rotate: 360 }} transition={{ duration: 1, repeat: Infinity, ease: 'linear' }}
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

  const { cognitive, face, answers, series, failovers, emotionSlices, stressSlices,
          tMin, tMax, ribbon } = timeline

  // A withdrawn channel is not read, so its emptiness needs its own sentence, never "no samples".
  // Absent (an older payload) reads as included: there is nothing true to say about it.
  const channels = data?.channels || {}
  const withheld = (included, revokedAt, sensor) => {
    if (included !== false) return null
    if (channels.consent_retrieved === false) return `Consent for ${sensor} could not be checked, so its data is not shown.`
    const Sensor = `${sensor[0].toUpperCase()}${sensor.slice(1)}`
    // No revocation date: never consented, so nothing was turned off and nothing was recorded.
    const since = revokedAt && fmtDate(revokedAt)
    if (!since) return `${Sensor} is off for this student, so nothing from it is shown.`
    return `${Sensor} was turned off on ${since}, so nothing it recorded is shown.`
  }
  const faceWithheld  = withheld(channels.face_included, channels.emotion_revoked_at, 'the camera')
  const faceCount = channels.face_included !== false ? face.length
    : channels.consent_retrieved === false ? 'Unavailable' : 'Off'
  const heartWithheld = withheld(channels.heart_included, channels.heart_revoked_at, 'heart-rate recording')

  const totalAnswers   = answers.length
  const correctAnswers = answers.filter(a => a.correct).length
  const acc = totalAnswers ? Math.round((correctAnswers / totalAnswers) * 100) : 0

  // Mount an axis only for shown series: Recharts throws on a line naming a missing axis.
  const axisShown = (axis) => shownSeries.some((s) => s.axis === axis)

  const hasChart = series.length >= 2

  // url | 'empty' (drew nothing) | 'unavailable' (object unreadable) |
  // 'unarchived' | 'failed' (request) | 'pending'. Failure never reads as empty.
  const archivedChart = (name) => {
    if (archiveErr) return 'failed'
    if (!archive) return 'pending'
    if ((archive.withdrawn || []).includes(name)) return 'withdrawn'
    if ((archive.unchecked || []).includes(name)) return 'unchecked'
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
    withdrawn: 'Not shown: this sensor is off for this student.',
    unchecked: 'Not shown: consent for this sensor could not be checked.',
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

      <m.div initial={{ opacity: 0, y: -8 }} animate={{ opacity: 1, y: 0 }}>
        <h1 className="text-3xl font-black text-gray-900 dark:text-white">Session Review</h1>
        <p className="text-gray-500 dark:text-gray-400 mt-1 text-sm font-mono break-all">id: {sessionId}</p>
      </m.div>

      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        {[
          { label: 'Cognitive samples', value: cognitive.length, icon: <Brain size={16} className="text-indigo-500" /> },
          // A skipped channel was never read, so its count is a reason, never 0.
          { label: 'Face samples',      value: faceCount,        icon: <Camera size={16} className="text-pink-500" /> },
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
        {heartWithheld && <p className="text-xs text-gray-600 dark:text-gray-400 -mt-2 mb-3">{heartWithheld}</p>}
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
                  <>
                    <ArchivedChart url={archivedChart('cognitive_timeline')} label="Focus and EEG stress" />
                    {/* Stored archives draw stress, not calm; they are never re-rendered. */}
                    <p className="text-[11px] text-gray-600 dark:text-gray-400">
                      Archived chart: the line its legend calls &ldquo;stress&rdquo; is EEG stress, the inverse of calm.
                    </p>
                  </>
                )}
                {isUrl(archivedChart('heart_rate')) && (
                  <ArchivedChart url={archivedChart('heart_rate')} label="Heart rate and heart-rate variability" />
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
              <LineChart data={chartRows} margin={{ top: 5, right: 20, left: 0, bottom: 5 }}>
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
                {/* bpm/ms get their own axis; each mounts only while a shown series uses it. */}
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
                        name={s.name} stroke={s.colour} dot={s.dot} strokeDasharray={s.dash}
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
              {faceWithheld || (isUrl(archivedChart('emotion_pie'))
                ? 'The per-sample rows have expired, so the moment-by-moment timeline is gone. The emotion mix is below.'
                : ['unavailable', 'withdrawn', 'unchecked'].includes(archivedChart('emotion_pie'))
                  ? NO_CHART_COPY[archivedChart('emotion_pie')]
                  : archivedChart('emotion_pie') === 'empty'
                    ? 'Nothing was recorded on the camera channel.'
                    : 'No face samples for this session.')}
            </p>
          </div>
        ) : (
          <div className="flex gap-1 overflow-x-auto pb-2">
            {ribbon.map((r, i) => (
              <div key={i} title={`${fmtTime(r.t)} — ${r.emotion || 'unknown'}`}
                   className="flex flex-col items-center text-xs flex-shrink-0 w-14">
                {emotionEmoji(r.emotion)
                  ? <span className="text-2xl">{emotionEmoji(r.emotion)}</span>
                  : <span className="h-8 flex items-center text-[10px] font-bold text-gray-700 dark:text-gray-300">
                      {r.emotion || 'unknown'}</span>}
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
                        <Cell key={sl.name} fill={EMOTION_COLOURS[sl.name] || UNKNOWN_EMOTION_COLOUR} />
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
              {/* The heart measure's own name: never "stress", which the EEG calm score is stored as. */}
              <h2 className="font-black text-gray-900 dark:text-white mb-3 text-sm">{MEASURES.body_arousal.name}</h2>
              {stressSlices.length === 0 ? (
                isUrl(archivedChart('stress_pie'))
                  ? <ArchivedChart url={archivedChart('stress_pie')} label={MEASURES.body_arousal.name} />
                  : <p className="text-sm text-gray-600 py-6 text-center dark:text-gray-400">{NO_CHART_COPY.unavailable}</p>
              ) : (
              <AccessibleChart className="h-52"
                {...sliceSpec(MEASURES.body_arousal.name, stressSlices, 'windows', { rowLabel: 'Band' })}>
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