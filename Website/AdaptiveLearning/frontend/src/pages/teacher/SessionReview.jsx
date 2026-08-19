import { useEffect, useState } from 'react'
import { useParams, useLocation, Link } from 'react-router-dom'
import { motion } from 'framer-motion'
import { ArrowLeft, Brain, Camera, CheckCircle2, XCircle, Activity } from 'lucide-react'
import {
  LineChart, Line, XAxis, YAxis, Tooltip,
  CartesianGrid, ReferenceLine, Legend, PieChart, Pie, Cell
} from 'recharts'
import AccessibleChart from '../../components/charts/AccessibleChart'
import { asPercent, describeSlices } from '../../components/charts/describeSeries'
import { apiFetch } from '../../lib/api'

// Fixed per label, so a colour means the same thing between two sessions --
// index-based colours would repaint the same emotion differently whenever the
// set of labels present changed. Keyed on the FER+ labels the backend
// actually stores (EEGResearch/src/app/services/face_emotion.py), the same
// ones the EMOJI map below uses -- not the -happiness/-sadness/-anger nouns,
// which never match a real sample and silently fell back to neutral's grey.
const EMOTION_COLOURS = {
  neutral: '#94a3b8', happy: '#10b981', surprise: '#38bdf8',
  sad: '#6366f1', angry: '#f43f5e', disgust: '#84cc16',
  fear: '#a855f7', contempt: '#f59e0b',
}

// `calibrating` and `unknown` are muted rather than absent: they are states the
// session was genuinely in, and dropping them would overstate how much of it
// was categorised.
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

// What the archived SVG is called, per section, so a fallback never lands
// under a heading promising a different view. The emotion *ribbon* has no
// archived equivalent -- it shows sequence and the archive only kept the pie --
// so that section deliberately maps to nothing and says so instead of
// borrowing the pie.
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
  // Where "Back" goes, from where the teacher came. Both links were hardcoded
  // to `/teacher/live`, so opening a session from the Sessions history sent
  // them to Live Monitoring on the way out -- a different page, with their
  // place in the list lost. `StudentReport.jsx` already carries this pattern.
  //
  // Falls back to Live rather than to `history.back()`: a teacher who arrived
  // by pasting a link has no history to go back to, and Live is where the
  // session ids are most likely to have come from.
  const location = useLocation()
  const backTo = location.state?.from || '/teacher/live'
  const [data, setData] = useState(null)
  const [err, setErr]   = useState(null)
  // Which session the state below belongs to. `loading` falls out of comparing
  // it to the session in the URL, so arriving at a different session raises the
  // skeleton on the render that changes the id rather than one effect later.
  const [loadedFor, setLoadedFor] = useState(null)
  const loading = loadedFor !== sessionId
  // The archived charts, fetched only when there are no raw rows left. Kept
  // separate from `data` because a failure here must not become the page's
  // error state: the answers, the tiles and the accuracy above do not depend
  // on it, and blanking all of them over a missing picture would be the
  // opposite of what this fallback is for.
  const [archive, setArchive] = useState(null)
  const [archiveErr, setArchiveErr] = useState(false)

  useEffect(() => {
    let killed = false
    // Returns one session's raw facial samples, plotted below, and
    // deliberately does NOT honour the facial-recognition switch in
    // lib/viewPrefs.js -- that control covers the reporting surfaces, which
    // render it, and this page does not. See the scope note there before
    // wiring it in.
    apiFetch(`/api/signals/session/${sessionId}`)
      .then(d => {
        if (killed) return
        setData(d)
        // No archive reset here any more: this component remounts per session
        // id, so there is no previous session's archive to clear. Left in, it
        // read as protection this no longer needs and the next reader would
        // have had to work out which mechanism was load-bearing.
        // Only when every channel is empty. That is precisely the expired
        // case -- `expire_signal_rows` takes all three channels for a day at
        // once and leaves the objects -- so a session that still has rows
        // never pays for the extra call, and one channel merely being off
        // does not trigger it. Erasure is the other way round: it deletes the
        // objects too, so there would be nothing to fall back to anyway.
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

  // numeric ms x-axis — way more stable than category strings
  const cognitiveByT = new Map(
    cognitive
      .map(c => {
        const t = new Date(c.ts).getTime()
        return Number.isFinite(t) ? [t, c] : null
      })
      .filter(Boolean),
  )

  // Heart merged into the same rows by nearest timestamp rather than plotted
  // from its own array: Recharts wants one dataset, and the two channels are
  // sampled at different rates. Nulls elsewhere, with `connectNulls` on the
  // line, so a sparse heart series draws as a line rather than as dots.
  const heartByT = heart
    .map(h => ({ t: new Date(h.ts).getTime(), h }))
    .filter(x => Number.isFinite(x.t))
    .sort((a, b) => a.t - b.t)

  // Row timestamps are the union of both channels, not cognitive alone: a
  // student can consent to the heart sensor without EEG (independent
  // switches -- ConsentChannels.jsx), in which case cognitive is empty and
  // heart is not. Building the base series from cognitive only left that
  // session with zero rows to ever merge a heart reading onto, so the bpm
  // axis, lines and failover markers below silently never rendered even
  // though real heart data existed.
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

  // Each heart reading lands on **one** row: the row nearest to it.
  //
  // This used to iterate the rows instead, giving every row its nearest reading
  // within 15s. Cognitive arrives at 4Hz, so a single heart reading was copied
  // onto up to 120 consecutive rows and drew as half a minute of dead-flat
  // line -- exactly the "carrying a reading across a gap" artefact the window
  // was added to prevent, produced by the window itself. A session with one
  // accepted window (poor electrode contact, say) looked like a heart that did
  // not vary rather than a heart measured once.
  //
  // One reading, one point. `dot` on the lines below is what makes a lone
  // reading visible, since a single point with no neighbours draws no segment.
  const rowIndexByT = series.map(r => r.t)
  let heartCollisions = 0
  for (const { t, h } of heartByT) {
    // Binary search for the insertion point, then compare the two neighbours --
    // "nearest" in both directions, not the last one at-or-before.
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
    // Still bounded. A reading with no row within 15s belongs to a stretch the
    // cognitive channel was not recording, and pinning it to the nearest row
    // minutes away would place a measurement at a time it was not taken.
    if (pick === null || Math.min(dBefore, dAfter) > 15_000) continue
    const row = series[pick]
    const d = Math.min(dBefore, dAfter)
    // Two readings can choose the same row -- `heart_signals` is unique per
    // (session, source, ts), so a headband and a camera reading the same
    // instant are two rows with one timestamp, and the union above collapses
    // them to one point. The write used to be unconditional, so the second
    // silently replaced the first and the chart showed one of the two sensors
    // with nothing saying a reading had been dropped.
    //
    // Closest wins, first wins a tie, and the loser is counted rather than
    // vanishing. Not merged or averaged: two sensors' calibrations combined
    // into one number is the same mistake the failover markers below exist to
    // avoid, one layer down.
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

  // Where the sensor changed mid-session. Marked rather than spliced: two
  // sensors' calibrations in one continuous trace reads as a physiological
  // event that did not happen.
  const failovers = []
  for (let i = 1; i < heartByT.length; i++) {
    if (heartByT[i].h.source && heartByT[i].h.source !== heartByT[i - 1].h.source) {
      failovers.push({ t: heartByT[i].t, source: heartByT[i].h.source })
    }
  }

  // Proportion, which the ribbon below cannot show: it shows sequence. The
  // bucketing it already does is deliberately not reused here -- a pie of
  // bucketed samples would weight a 10 s bucket the same as a 2 s one.
  const emotionSlices = Object.entries(
    face.reduce((acc, f) => {
      if (!f.emotion) return acc          // a rejected window is not a reading
      acc[f.emotion] = (acc[f.emotion] || 0) + 1
      return acc
    }, {}),
  ).map(([name, value]) => ({ name, value }))

  const stressSlices = Object.entries(
    heart.reduce((acc, h) => {
      // `calibrating` and `unknown` are kept as slices rather than dropped:
      // a pie that silently omits them claims a session was entirely
      // categorised when part of it was not.
      const key = h.stress_category || 'unknown'
      acc[key] = (acc[key] || 0) + 1
      return acc
    }, {}),
  ).map(([name, value]) => ({ name, value }))

  const tMin = series.length ? series[0].t : 0
  const tMax = series.length ? series[series.length - 1].t : 0

  // emotion ribbon — bucket every ~10s
  const ribbon = []
  let lastBucket = 0
  face.forEach(f => {
    const t = new Date(f.ts).getTime()
    if (Number.isFinite(t) && t - lastBucket > 10_000) {
      // Emotion only. `attention` has no producer, so carrying it here put a
      // permanently-null field on every ribbon segment.
      ribbon.push({ t, emotion: f.emotion })
      lastBucket = t
    }
  })

  const totalAnswers   = answers.length
  const correctAnswers = answers.filter(a => a.correct).length
  const acc = totalAnswers ? Math.round((correctAnswers / totalAnswers) * 100) : 0

  // One spec, driving the summary sentence and the sr-only table alike.
  //
  // `asPercent` because this page's cognitive rows are raw 0..1 ratios -- the
  // chart plots them against `domain={[0, 1]}` -- so describing them with a `%`
  // unit and no scaling announced a session ranging 42-78% as "Focus 0% to 1%".
  // `SignalPanel` scales on the way into its chart data and so never hit this;
  // here the chart wants ratios and only the text wants percentages.
  //
  // `heart_rate_bpm` and `rmssd_ms` are the field names the chart plots. Named
  // `bpm` at first, which made a visibly-drawn heart line read as "not
  // recorded" in both the sentence and the table -- and RMSSD was absent from
  // the table entirely while being plotted beside it.
  const TIMELINE_COLUMNS = [
    { key: 'focus',          label: 'Focus',      unit: '%',    scale: asPercent },
    { key: 'stress',         label: 'EEG stress', unit: '%',    scale: asPercent },
    { key: 'engagement',     label: 'Engagement', unit: '%',    scale: asPercent },
    { key: 'heart_rate_bpm', label: 'Heart rate', unit: ' bpm' },
    { key: 'rmssd_ms',       label: 'RMSSD',      unit: ' ms' },
  ]

  const hasChart = series.length >= 2

  // The archive's four states, kept apart rather than collapsed into "is there
  // a picture". Only the first two are facts about the session; the other three
  // are facts about the archive, and a surface that reports them as "recorded
  // nothing" is the absence-as-data failure the payload was shaped to prevent.
  //
  //   url          -- archived and still readable
  //   'empty'      -- the archive ran and this channel drew nothing
  //   'unavailable'-- a path was recorded and the object could not be read
  //   'unarchived' -- the archive never ran (closed before Phase 8)
  //   'failed'     -- we asked and the request did not come back
  //   'pending'    -- we have not finished asking
  //
  // The last two used to share one 'unknown' state, and that state's copy was
  // the *same sentence* as 'unarchived' -- "No signal samples for this
  // session." So a backend hiccup, a stale 403, or simply the moment before the
  // request lands all told a teacher the session recorded nothing, which is an
  // absence asserted from data that never arrived: the one thing every other
  // surface here is shaped to avoid. Five states, five sentences.
  const archivedChart = (name) => {
    if (archiveErr) return 'failed'
    if (!archive) return 'pending'
    if (!archive.archived) return 'unarchived'
    if ((archive.unavailable || []).includes(name)) return 'unavailable'
    const url = (archive.charts || {})[name]
    return url || 'empty'
  }

  // One sentence for the states that are not a URL, so each empty block says
  // the same true thing rather than each inventing its own wording.
  // One sentence per state, and no two of them the same. `unarchived` and the
  // old `unknown` shared this string, which made "we could not find out" read
  // as "there was nothing" -- and left a teacher no way to tell a session that
  // predates chart archiving from one whose charts simply failed to load, where
  // the useful next action is to try again.
  const NO_CHART_COPY = {
    empty: 'Nothing was recorded on this channel.',
    unavailable: 'The archived chart for this session could not be loaded.',
    unarchived: 'No signal samples for this session.',
    failed: 'The archived charts could not be loaded — try again.',
    pending: 'Loading the archived charts…',
  }

  const isUrl = (v) => typeof v === 'string' && v.startsWith('http')

  // A section can cover more than one chart -- the timeline block draws both
  // cognitive and heart -- and the backend signs each object independently, so
  // the states genuinely differ across a set: `cognitive_timeline` empty while
  // `heart_rate` is unavailable is an ordinary outcome, not a corner case.
  //
  // A fault therefore outranks an absence whenever they are mixed. Reading the
  // state of one member and speaking for the whole set is how "the object could
  // not be read" becomes "nothing was recorded", which is the single thing this
  // payload's shape exists to prevent.
  const anyUnavailable = (names) => names.some(n => archivedChart(n) === 'unavailable')

  // The copy for a set with no drawable chart in it, fault first.
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

      {/* summary tiles */}
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

      {/* main timeseries chart */}
      <div className="bg-white dark:bg-gray-900 rounded-2xl border border-gray-100 dark:border-gray-800 shadow-sm p-5">
        <h2 className="font-black text-gray-900 dark:text-white mb-4 flex items-center gap-2">
          <Brain size={18} className="text-indigo-600" /> Cognitive timeline
        </h2>
        {!hasChart ? (
          <div className="text-center py-12">
            {/* The fallback. Once `expire_signal_rows` has taken the
                per-sample rows on `ends_on`, the archived SVG is the only
                remaining view of this session -- so an empty state here would
                be reporting an absence that is contradicted by a picture
                sitting in the bucket. Both archived charts are shown, because
                the live version of this section merges cognitive and heart
                into one trace and the archive keeps them apart. */}
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
                {/* One chart drawn, the other unreadable. Saying nothing here
                    would present a partial view as the whole session. */}
                {anyUnavailable(['cognitive_timeline', 'heart_rate']) && (
                  <p className="text-[11px] text-amber-600 dark:text-amber-500 mt-2">
                    One archived chart for this session could not be loaded.
                  </p>
                )}
              </>
            ) : (
              <>
                <div className="text-5xl mb-2">🧠</div>
                {/* Now reachable from heart-only data too (series merges both
                    channels' timestamps), so this can no longer name just the
                    one channel it used to be the only source for. */}
                <p className="text-sm text-gray-400">
                  {noChartCopy(['cognitive_timeline', 'heart_rate'])}
                </p>
                {/* Only under the empty branch. Beside an archived chart it
                    would be telling a teacher to wait for a stream on a
                    session that ended months ago. */}
                <p className="text-[11px] text-gray-400 mt-1">Once a sensor starts streaming, it'll show up here.</p>
              </>
            )}
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
                {/* Two axes with explicit ids. The existing one is ratio-scaled;
                    bpm (~40-180) and RMSSD (ms) do not belong on it. Adding a
                    second axis without giving the first an id silently rebinds
                    every existing series to the new one. */}
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
                {/* Named "EEG stress" rather than bare "stress": this is
                    cognitive_signals.stress (inverted calm), a different
                    measurement from the heart-derived stress_category pie
                    below, and CLAUDE.md is explicit that the two must never
                    share a label. */}
                <Line yAxisId="ratio" type="monotone" dataKey="stress" name="EEG stress" stroke="#f43f5e" dot={false} connectNulls isAnimationActive={false} />

                {/* Omitted rather than drawn as an all-null line: an empty
                    legend entry reads as a measurement that flatlined. */}
                {/* `dot` on, unlike the cognitive lines above. Heart is a held
                    25s window recomputed every 10s, so a session yields tens of
                    readings where cognitive yields thousands -- and each one now
                    occupies a single row. Without dots an isolated reading has
                    no neighbour to draw a segment to and renders as nothing at
                    all, which is how "measured once" would look identical to
                    "never measured". */}
                {hasHeart && (
                  <Line yAxisId="abs" type="monotone" dataKey="heart_rate_bpm" name="Heart rate (bpm)"
                        stroke="#a855f7" dot={{ r: 2 }} connectNulls isAnimationActive={false} />
                )}
                {hasHeart && (
                  <Line yAxisId="abs" type="monotone" dataKey="rmssd_ms" name="RMSSD (ms)"
                        stroke="#f59e0b" dot={{ r: 2 }} connectNulls isAnimationActive={false} />
                )}

                {/* Sensor changed here. Distinct from the answer markers below
                    by colour and by being solid. Gated on hasHeart, not just
                    failovers.length: failovers is built from raw heart_signals
                    source changes and can be non-empty (every window rejected
                    but still carrying a source) while no row ever got a
                    numeric heart_rate_bpm -- and the "abs" axis these markers
                    reference only mounts when hasHeart is true. */}
                {hasHeart && failovers.map((f, i) => (
                  <ReferenceLine key={`fo-${i}`} yAxisId="abs" x={f.t}
                                 stroke="#a855f7" strokeOpacity={0.5} />
                ))}

                {/* answer markers as vertical reference lines (numeric x is safe) */}
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
          </AccessibleChart>
        )}
        {hasChart && answers.length > 0 && (
          <p className="text-[11px] text-gray-400 mt-2">
            Vertical lines = answer events · <span className="text-emerald-500">green</span> correct ·{' '}
            <span className="text-rose-500">red</span> incorrect
          </p>
        )}
      </div>

      {/* emotion ribbon */}
      <div className="bg-white dark:bg-gray-900 rounded-2xl border border-gray-100 dark:border-gray-800 shadow-sm p-5">
        <h2 className="font-black text-gray-900 dark:text-white mb-4 flex items-center gap-2">
          <Camera size={18} className="text-pink-600" /> Emotion timeline
        </h2>
        {ribbon.length === 0 ? (
          <div className="text-center py-8">
            <div className="text-4xl mb-2">📷</div>
            {/* No archived equivalent, deliberately. This section shows
                sequence and the archive only kept the proportion pie, so
                borrowing `emotion_pie` here would answer a different question
                under this heading. The pie is rendered below, where it says
                what it is. */}
            {/* Compared against the state, not against the rendered string.
                Testing `NO_CHART_COPY[x] === NO_CHART_COPY.empty` worked only
                as long as no two states shared wording -- and two of them
                already do, so it was one copy edit away from silently
                misreporting. */}
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

      {/* Proportion beside sequence. The ribbon above shows the order emotions
          came in; these show how much of the session each accounted for, which
          the ribbon cannot -- a single bad stretch and a whole bad session look
          alike scrolling through emojis. Rendered only when there is something
          to render: an empty pie is a claim about a session that recorded
          nothing, which an unread channel has not earned. */}
      {/* `unavailable` keeps the section mounted rather than hiding it. A
          hidden section reports nothing at all, which is how an unreadable
          stress chart would have gone unmentioned on every surface -- the
          absence swallowing the fault, one level up. */}
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
              <AccessibleChart className="h-52"
                summary={describeSlices('Emotion mix', emotionSlices, 'samples')}
                rows={emotionSlices} rowKey="name" rowLabel="Emotion"
                columns={[{ key: 'value', label: 'Samples' }]}>
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
              </AccessibleChart>
              )}
            </div>
          )}
          {(stressSlices.length > 0 || isUrl(archivedChart('stress_pie'))
            || archivedChart('stress_pie') === 'unavailable') && (
            <div className="bg-white dark:bg-gray-900 rounded-2xl border border-gray-100 dark:border-gray-800 shadow-sm p-5">
              {/* Not bare "Stress": heart_signals.stress_category is a
                  physiological measurement, distinct from the EEG-derived
                  "EEG stress" series in the timeline chart above. CLAUDE.md:
                  never render both under one "Stress" label. */}
              <h2 className="font-black text-gray-900 dark:text-white mb-3 text-sm">Heart-rate stress</h2>
              {stressSlices.length === 0 ? (
                isUrl(archivedChart('stress_pie'))
                  ? <ArchivedChart url={archivedChart('stress_pie')} label="Autonomic arousal" />
                  : <p className="text-sm text-gray-400 py-6 text-center">{NO_CHART_COPY.unavailable}</p>
              ) : (
              <AccessibleChart className="h-52"
                summary={describeSlices('Heart-rate stress', stressSlices, 'windows')}
                rows={stressSlices} rowKey="name" rowLabel="Band"
                columns={[{ key: 'value', label: 'Windows' }]}>
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
              </AccessibleChart>
              )}
            </div>
          )}
        </div>
      )}

      {/* answers table */}
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