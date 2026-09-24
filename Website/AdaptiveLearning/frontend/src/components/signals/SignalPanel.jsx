import { Activity, Brain, Heart, Radio, Sparkles, Zap } from 'lucide-react'
import ScaleNote from './ScaleNote'
import { combineScales } from '../../lib/scoreScale'
import {
  LineChart, Line, XAxis, YAxis, CartesianGrid,
  PieChart, Pie, Cell, Legend,
} from 'recharts'
import ChartTooltip from '../charts/ChartTooltip'
import { sliceSpec } from '../charts/describeSeries'
import AccessibleChart from '../charts/AccessibleChart'
import SeriesFilter from '../charts/SeriesFilter'
import { useSeriesFilter } from '../../hooks/useSeriesFilter'

// Why a channel has no value. Never "no data" for something never recorded.
const CHANNEL_STATE = {
  revoked: since => (since ? `Off since ${since}` : 'Not recorded'),
  // Consent read failed: we can't claim the student turned it off.
  unknown: () => 'Unavailable',
  // Samples arrived, none usable.
  calibrating: () => 'Calibrating',
  noSensor: () => 'No sensor',
}

// Short date, or null (never "Invalid Date") when there is none.
function shortDate(iso) {
  if (!iso) return null
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? null : d.toLocaleDateString(undefined,
    { day: 'numeric', month: 'short' })
}

/** Tile text for a channel with no value: consent unreadable, revoked, calibrating, or no sensor. */
export function offLabel({ on, revokedAt, consentRetrieved, samples }) {
  if (consentRetrieved === false) return CHANNEL_STATE.unknown()
  if (!on) return CHANNEL_STATE.revoked(shortDate(revokedAt))
  return samples > 0 ? CHANNEL_STATE.calibrating() : CHANNEL_STATE.noSensor()
}

/**
 * A rendered value, or the reason there isn't one.
 * Every tile goes through here, so `pct()`'s 'N/A' never reaches the screen.
 */
export function valueOrReason(value, reason) {
  return (value && value !== 'N/A') ? value : offLabel(reason)
}

// Fixed per label, not by slice order, so a colour always means one emotion.
const EMOTION_COLOURS = {
  happy: '#10b981', neutral: '#94a3b8', surprise: '#f59e0b',
  sad: '#6366f1', anger: '#ef4444', fear: '#8b5cf6',
  disgust: '#14b8a6', contempt: '#f97316',
}

// muse_optics / muse_ppg / rppg are storage values, not display strings.
const SOURCE_LABELS = {
  muse_optics: 'Headband (optical)',
  muse_ppg: 'Headband (PPG)',
  rppg: 'Camera',
}
function sourceLabel(source) {
  return SOURCE_LABELS[source] || source
}

// Signals arrive as 0..1 ratios. A finite number, or null: Number('') is 0,
// so a blank must not become a confident "0%".
function ratio(value) {
  if (value === null || value === undefined) return null
  if (typeof value === 'string' && value.trim() === '') return null
  const n = Number(value)
  return Number.isFinite(n) ? n : null
}

export function pct(value) {
  const n = ratio(value)
  return n === null ? 'N/A' : `${Math.round(n * 100)}%`
}

// Nulls stay null so a day with no data is a gap, not a line at zero.
function toPct(value) {
  const n = ratio(value)
  return n === null ? null : n * 100
}

// Absolute units (bpm); never through `toPct`.
function unit(value, suffix, digits = 0) {
  const n = ratio(value)
  return n === null ? 'N/A' : `${n.toFixed(digits)}${suffix}`
}

// `face_included` is the legacy alias; absent on both reads as on.
export function emotionOn(report) {
  if (report?.emotion_included !== undefined) return report.emotion_included !== false
  return report?.face_included !== false
}

function heartOn(report) {
  // Absent means a payload predating the heart channel.
  return report?.heart_included === true
}

function faceReason(report, faceOn) {
  return {
    on: faceOn,
    revokedAt: report?.emotion_revoked_at,
    consentRetrieved: report?.consent_retrieved,
    samples: report?.sample_counts?.face,
  }
}

/**
 * The offLabel reason for the EEG channel.
 * `eeg_enabled`, not `eeg_included`: cognitive is always read. Absent reads as on.
 */
function eegReason(report) {
  return {
    on: report?.eeg_enabled !== false,
    revokedAt: report?.eeg_revoked_at ?? null,
    consentRetrieved: report?.consent_retrieved,
    samples: report?.sample_counts?.cognitive,
  }
}

export function MiniMetric({ label, value, icon: Icon = Activity, tone = 'indigo' }) {
  const tones = {
    indigo: 'bg-indigo-50 text-indigo-700 dark:bg-indigo-900/30 dark:text-indigo-300',
    emerald: 'bg-emerald-50 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-300',
    rose: 'bg-rose-50 text-rose-700 dark:bg-rose-900/30 dark:text-rose-300',
    amber: 'bg-amber-50 text-amber-700 dark:bg-amber-900/30 dark:text-amber-300',
    sky: 'bg-sky-50 text-sky-700 dark:bg-sky-900/30 dark:text-sky-300',
  }
  return (
    <div className="rounded-2xl border border-gray-100 dark:border-gray-800 bg-white dark:bg-gray-900 p-4 shadow-sm">
      <div className="flex items-center justify-between gap-3">
        <div>
          <p className="text-xs font-bold uppercase tracking-widest text-gray-600 dark:text-gray-400">{label}</p>
          <p className="mt-1 text-2xl font-black text-gray-900 dark:text-white">{value}</p>
        </div>
        <div className={`p-2.5 rounded-xl ${tones[tone] || tones.indigo}`}>
          <Icon size={18} />
        </div>
      </div>
    </div>
  )
}

export function LiveSignalSummary({ report, title = 'Live Signal Snapshot' }) {
  const latest = report?.latest || {}
  const cog = latest.cognitive || {}
  const face = latest.face || {}
  const faceOn = emotionOn(report)
  // Absent when the channel wasn't read.
  const heart = latest.heart
  const heartShown = heartOn(report) && !!heart
  return (
    <div className="rounded-2xl border border-gray-100 dark:border-gray-800 bg-white dark:bg-gray-900 p-5 shadow-sm">
      <div className="mb-4 flex items-center justify-between gap-3">
        <div>
          <h3 className="font-black text-gray-900 dark:text-white">{title}</h3>
          <p className="text-xs text-gray-600 dark:text-gray-400">
            Most recent EEG{heartShown ? ', heart' : ''} and facial-recognition readings.
          </p>
        </div>
        <Radio size={18} className="text-emerald-500" />
      </div>
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
        <MiniMetric label="Focus" value={valueOrReason(pct(cog.focus), eegReason(report))}
                    icon={Brain} tone="emerald" />
        <MiniMetric label="Stress" value={valueOrReason(pct(cog.stress), eegReason(report))}
                    icon={Zap} tone="rose" />
        {/* No Engagement tile (it is focus) and no attention tile (no producer). */}
        {heartShown && (
          <MiniMetric label="Heart Rate" value={unit(heart.heart_rate_bpm, ' bpm')} icon={Heart} tone="rose" />
        )}
      </div>
      <div className="mt-4 grid gap-3 text-sm">
        <div className="rounded-xl bg-slate-50 dark:bg-gray-800 p-3">
          <p className="text-xs font-bold uppercase tracking-widest text-gray-600 dark:text-gray-400">Facial Emotion</p>
          <p className="font-bold text-gray-900 dark:text-white capitalize">
            {valueOrReason(faceOn && face.emotion, faceReason(report, faceOn))}
          </p>
        </div>
      </div>
    </div>
  )
}

/**
 * Week-over-week averages from `signal_daily_rollup`, which outlives the raw rows.
 * A failed read is not a quiet term; an empty week is a gap, not a missing bar.
 */
export function SignalTrend({ trend, title = 'Term Trend' }) {
  const heartShown = heartOn(trend)
  const weeks = trend?.weeks || []
  const failed = trend?.retrieved === false

  const chartData = weeks.map(w => ({
    ...w,
    focus: toPct(w.focus),
    stress: toPct(w.stress),
    heart_rate_bpm: ratio(w.heart_rate_bpm),
    // The Monday, as `MM-DD`.
    label: w.week_start ? w.week_start.slice(5) : '',
  }))

  // Lines, columns and toggles all derive from this list. Focus/stress are
  // scaled above, so no `scale` here.
  const SERIES = [
    { key: 'focus',  label: 'Focus',  unit: '%', colour: '#6366f1', axis: 'pct', name: 'Focus' },
    { key: 'stress', label: 'Stress', unit: '%', colour: '#f43f5e', axis: 'pct', name: 'Stress' },
    ...(heartShown
      ? [{ key: 'heart_rate_bpm', label: 'Heart rate', unit: ' bpm',
           colour: '#a855f7', axis: 'bpm', name: 'Heart Rate (bpm)' }]
      : []),
  ]

  const { hidden, toggle, showAll, shownOf } = useSeriesFilter()
  const shown = shownOf(SERIES)
  const COLUMNS = shown.map(({ key, label, unit }) => ({ key, label, unit }))
  // A line naming an unmounted axis throws, so axes follow what is shown.
  const axisShown = (axis) => shown.some((x) => x.axis === axis)

  // Coverage goes in the sentence so a thin week stays visibly thin.
  const recorded = weeks.filter(w => w.days_with_data > 0).length
  const headline = `Weekly signal averages across ${weeks.length} week`
    + `${weeks.length === 1 ? '' : 's'}, with data recorded on ${recorded} of them.`

  return (
    <div className="rounded-2xl border border-gray-100 dark:border-gray-800 bg-white dark:bg-gray-900 p-5 shadow-sm">
      {/* Says where a score-scale change falls in the term; otherwise nothing. */}
      <ScaleNote scale={combineScales(weeks)} what="The lines below" />
      <div className="mb-4">
        <h3 className="font-black text-gray-900 dark:text-white">{title}</h3>
        <p className="text-xs text-gray-600 dark:text-gray-400">
          Weekly averages, weighted by how much was recorded each day. Weeks
          with nothing recorded are left as gaps.
        </p>
      </div>

      {/* Outside the height-fixed box so the chips can wrap. */}
      {!failed && chartData.length > 0 && (
        <SeriesFilter series={SERIES} hidden={hidden} onToggle={toggle}
                      label="Measurements shown on the term trend" />
      )}
      <div className="h-56 rounded-2xl bg-slate-50 dark:bg-gray-800 p-3">
        {failed || chartData.length === 0 ? (
          <div className="h-full flex items-center justify-center text-sm text-gray-600 text-center px-4 dark:text-gray-400">
            {failed
              ? 'The term trend could not be loaded.'
              : 'No signal history yet.'}
          </div>
        ) : shown.length === 0 ? (
          /* Every series hidden: a claim about the view, not the data. */
          <div className="h-full flex flex-col items-center justify-center gap-3">
            <p className="text-sm text-gray-600 dark:text-gray-400">No measurements selected.</p>
            <button type="button" onClick={showAll}
                    className="px-3 py-2.5 min-h-[44px] rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-900 text-xs font-bold text-gray-900 dark:text-white hover:bg-slate-50 dark:hover:bg-gray-800 transition">
              Show all
            </button>
          </div>
        ) : (
          <AccessibleChart headline={headline} rows={chartData}
                           rowKey="label" rowLabel="Week of" columns={COLUMNS}>
            <LineChart data={chartData} margin={{ top: 10, right: 10, left: -20, bottom: 0 }}>
              <CartesianGrid strokeDasharray="3 3" opacity={0.25} />
              <XAxis dataKey="label" fontSize={11} tickLine={false} />
              {/* Explicit ids on both axes, or every series binds to the second. */}
              {axisShown('pct') && <YAxis yAxisId="pct" domain={[0, 100]} fontSize={11} tickLine={false} />}
              {axisShown('bpm') && (
                <YAxis yAxisId="bpm" orientation="right" domain={['auto', 'auto']}
                       fontSize={11} tickLine={false} unit=" bpm" />
              )}
              <ChartTooltip />
              {/* Dots, so a single recorded week is still visible. */}
              {shown.map((x) => (
                <Line key={x.key} yAxisId={x.axis} type="monotone" dataKey={x.key}
                      stroke={x.colour} strokeWidth={2} dot={{ r: 3 }} name={x.name}
                      connectNulls={false} />
              ))}
            </LineChart>
          </AccessibleChart>
        )}
      </div>
    </div>
  )
}


export function WeeklySignalReport({ report, title = 'Weekly EEG & Face Report' }) {
  const avg = report?.averages || {}
  const highlights = report?.highlights || {}
  const counts = report?.sample_counts || {}
  const faceOn = emotionOn(report)
  const heartShown = heartOn(report)
  // Ratios scaled to percent; heart rate stays in bpm.
  const chartData = (report?.daily || []).map(d => ({
    ...d,
    focus: toPct(d.focus),
    stress: toPct(d.stress),
    heart_rate_bpm: ratio(d.heart_rate_bpm),
    label: d.date ? d.date.slice(5) : '',
  }))
  // Days the row cap left unread; they draw as gaps like quiet days, so say so.
  const unretrieved = (report?.daily || []).filter(
    d => d.cognitive_retrieved === false
      || d.face_retrieved === false
      || d.heart_retrieved === false
      || d.sessions_retrieved === false
  ).length
  // `=== false`, not falsy: undefined is an older payload, null the facial opt-out.
  const retrieved = report?.retrieved || {}
  const cogFailed = retrieved.cognitive === false
  const faceFailed = retrieved.face === false
  const sessionsFailed = retrieved.sessions === false
  const heartFailed = retrieved.heart === false
  const anyFailed = cogFailed || faceFailed || heartFailed || sessionsFailed
  // Consent unreadable: "we couldn't find out", not "declined".
  const consentFailed = report?.consent_retrieved === false
  // Tells "measured but unusable" from "never measured".
  const heartSamples = counts.heart || 0
  const emotionSlices = Object.entries(report?.emotion_distribution || {})
    .map(([name, value]) => ({ name, value }))
    .sort((a, b) => b.value - a.value)

  // Lines, columns and toggles all derive from this list, so a column never
  // names an undrawn series. Palette pinned equal to SessionReview.jsx by a backend test.
  const TREND_SERIES = [
    { key: 'focus',  label: 'Focus',  unit: '%', colour: '#6366f1', axis: 'pct', name: 'Focus' },
    { key: 'stress', label: 'Stress', unit: '%', colour: '#f43f5e', axis: 'pct', name: 'Stress' },
    ...(heartShown
      ? [{ key: 'heart_rate_bpm', label: 'Heart rate', unit: ' bpm',
           colour: '#a855f7', axis: 'bpm', name: 'Heart Rate (bpm)' }]
      : []),
  ]

  const { hidden, toggle, showAll, shownOf } = useSeriesFilter()
  const shown = shownOf(TREND_SERIES)
  const TREND_COLUMNS = shown.map(({ key, label, unit }) => ({ key, label, unit }))
  const axisShown = (axis) => shown.some((x) => x.axis === axis)

  return (
    <div className="rounded-2xl border border-gray-100 dark:border-gray-800 bg-white dark:bg-gray-900 p-5 shadow-sm">
      <div className="mb-4">
        <h3 className="font-black text-gray-900 dark:text-white">{title}</h3>
        <p className="text-xs text-gray-600 dark:text-gray-400">Averages are based on the last {report?.days || 7} days of available samples.</p>
        {/* An average can mix both score scales with no visible step. */}
        <ScaleNote scale={avg.score_scale} what="The averages below" />
      </div>

      <div className="grid grid-cols-2 lg:grid-cols-5 gap-3 mb-5">
        <MiniMetric label="Avg Focus" value={pct(avg.focus)} icon={Brain} tone="emerald" />
        <MiniMetric label="Avg Stress" value={pct(avg.stress)} icon={Zap} tone="rose" />
        {/* sessions_recorded: sample_counts.sessions is capped. Dash on a failed read, never 0. */}
        <MiniMetric
          label="Sessions"
          value={sessionsFailed ? '—' : (report?.sessions_recorded ?? counts.sessions ?? 0)}
          icon={Radio}
          tone="amber"
        />
      </div>

      {chartData.length > 0 && (
        <SeriesFilter series={TREND_SERIES} hidden={hidden} onToggle={toggle}
                      label="Measurements shown on the daily trend" />
      )}
      <div className="h-56 rounded-2xl bg-slate-50 dark:bg-gray-800 p-3">
        {chartData.length === 0 ? (
          <div className="h-full flex items-center justify-center text-sm text-gray-600 text-center px-4 dark:text-gray-400">
            {anyFailed
              ? 'Weekly signal data could not be loaded.'
              : 'No weekly signal data available yet.'}
          </div>
        ) : shown.length === 0 ? (
          /* Every series hidden: a claim about the view, not the data. */
          <div className="h-full flex flex-col items-center justify-center gap-3">
            <p className="text-sm text-gray-600 dark:text-gray-400">No measurements selected.</p>
            <button type="button" onClick={showAll}
                    className="px-3 py-2.5 min-h-[44px] rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-900 text-xs font-bold text-gray-900 dark:text-white hover:bg-slate-50 dark:hover:bg-gray-800 transition">
              Show all
            </button>
          </div>
        ) : (
          <AccessibleChart
            headline={`Daily signal trend over ${chartData.length} day${chartData.length === 1 ? '' : 's'}.`}
            rows={chartData} rowKey="label" rowLabel="Day"
            columns={TREND_COLUMNS}>
            <LineChart data={chartData} margin={{ top: 10, right: 10, left: -20, bottom: 0 }}>
                <CartesianGrid strokeDasharray="3 3" opacity={0.25} />
                <XAxis dataKey="label" fontSize={11} tickLine={false} />
                {/* Percent left, bpm right. Explicit ids on both, or every series binds to the second. */}
                {axisShown('pct') && <YAxis yAxisId="pct" domain={[0, 100]} fontSize={11} tickLine={false} />}
                {axisShown('bpm') && (
                  <YAxis yAxisId="bpm" orientation="right" domain={['auto', 'auto']}
                         fontSize={11} tickLine={false} unit=" bpm" />
                )}
                <ChartTooltip />
                {/* Dots, so a single recorded day is still visible. */}
                {shown.map((x) => (
                  <Line key={x.key} yAxisId={x.axis} type="monotone" dataKey={x.key}
                        stroke={x.colour} strokeWidth={2} dot={{ r: 3 }} name={x.name} />
                ))}
              </LineChart>
          </AccessibleChart>
        )}
      </div>

      <div className="mt-4 grid md:grid-cols-3 gap-3 text-sm">
        <div className="rounded-xl bg-slate-50 dark:bg-gray-800 p-3">
          <p className="text-xs font-bold uppercase tracking-widest text-gray-600 dark:text-gray-400">Highest Stress</p>
          <p className="font-bold text-gray-900 dark:text-white">{pct(highlights.highest_stress)}</p>
        </div>
        <div className="rounded-xl bg-slate-50 dark:bg-gray-800 p-3">
          <p className="text-xs font-bold uppercase tracking-widest text-gray-600 dark:text-gray-400">Lowest Focus</p>
          <p className="font-bold text-gray-900 dark:text-white">{pct(highlights.lowest_focus)}</p>
        </div>
        <div className="rounded-xl bg-slate-50 dark:bg-gray-800 p-3">
          <p className="text-xs font-bold uppercase tracking-widest text-gray-600 dark:text-gray-400">Dominant Emotion</p>
          <p className="font-bold text-gray-900 dark:text-white capitalize">
            {valueOrReason(faceOn && highlights.dominant_emotion, faceReason(report, faceOn))}
          </p>
        </div>
      </div>

      {/* Off (`=== false`) keeps the row with the reason; absent omits it. */}
      {report?.heart_included === false && (
        <div className="mt-3 rounded-xl bg-slate-50 dark:bg-gray-800 p-3 text-sm">
          <p className="text-xs font-bold uppercase tracking-widest text-gray-600 dark:text-gray-400">Heart</p>
          <p className="font-bold text-gray-900 dark:text-white">
            {offLabel({
              on: false,
              revokedAt: report?.heart_revoked_at,
              consentRetrieved: report?.consent_retrieved,
              samples: report?.sample_counts?.heart,
            })}
          </p>
        </div>
      )}
      {heartShown && (
        <div className="mt-3 grid md:grid-cols-3 gap-3 text-sm">
          <div className="rounded-xl bg-slate-50 dark:bg-gray-800 p-3">
            <p className="text-xs font-bold uppercase tracking-widest text-gray-600 dark:text-gray-400">Avg Heart Rate</p>
            <p className="font-bold text-gray-900 dark:text-white">
              {valueOrReason(unit(highlights.heart_rate_bpm, ' bpm'), {
                on: true,
                consentRetrieved: report?.consent_retrieved,
                samples: report?.sample_counts?.heart,
              })}
            </p>
          </div>
          <div className="rounded-xl bg-slate-50 dark:bg-gray-800 p-3">
            <p className="text-xs font-bold uppercase tracking-widest text-gray-600 dark:text-gray-400">Avg RMSSD</p>
            <p className="font-bold text-gray-900 dark:text-white">
              {valueOrReason(unit(highlights.rmssd_ms, ' ms'), {
                on: true,
                consentRetrieved: report?.consent_retrieved,
                samples: report?.sample_counts?.heart,
              })}
            </p>
          </div>
          <div className="rounded-xl bg-slate-50 dark:bg-gray-800 p-3">
            <p className="text-xs font-bold uppercase tracking-widest text-gray-600 dark:text-gray-400">Sensor</p>
            {/* Named: accuracy differs by source, and the camera is unvalidated. */}
            <p className="font-bold text-gray-900 dark:text-white">
              {(report?.heart_sources || []).length
                ? report.heart_sources.map(sourceLabel).join(', ')
                : offLabel({
                    on: true,
                    consentRetrieved: report?.consent_retrieved,
                    samples: report?.sample_counts?.heart,
                  })}
            </p>
          </div>
        </div>
      )}

      {/* The distribution, not just `dominant_emotion`. */}
      {faceOn && emotionSlices.length > 0 && (
        <div className="mt-4">
          <p className="text-xs font-bold uppercase tracking-widest text-gray-600 mb-1 dark:text-gray-400">Emotion Mix</p>
          <AccessibleChart className="h-52"
            {...sliceSpec('Emotion mix', emotionSlices, 'samples', { rowLabel: 'Emotion' })}>
            <PieChart>
                  <Pie data={emotionSlices} dataKey="value" nameKey="name"
                       innerRadius="45%" outerRadius="75%" paddingAngle={2}>
                    {emotionSlices.map(slice => (
                      <Cell key={slice.name} fill={EMOTION_COLOURS[slice.name] || '#94a3b8'} />
                    ))}
                  </Pie>
                  <ChartTooltip formatter={(v, n) => [`${v} samples`, n]} />
                  <Legend />
                </PieChart>
          </AccessibleChart>
        </div>
      )}

      <p className="mt-4 text-sm text-gray-500 dark:text-gray-400">{report?.summary || 'No summary available yet.'}</p>
      {report && !faceOn && (
        <p className="mt-1 text-xs text-gray-600 dark:text-gray-400">
          Facial recognition data was not included in this report.
        </p>
      )}
      {/* Samples but no average: every reading failed the quality gate. */}
      {heartShown && heartSamples > 0 && ratio(highlights.heart_rate_bpm) === null && (
        <p className="mt-1 text-xs text-gray-600 dark:text-gray-400">
          Heart-rate samples were recorded but none met the quality threshold, so no average is shown.
        </p>
      )}
      {consentFailed && (
        <p className="mt-1 text-xs text-amber-600 dark:text-amber-400">
          Consent settings could not be read, so heart and facial data were left out of this report — that is not a record of what was permitted.
        </p>
      )}
      {/* Per table: the reads fail independently. */}
      {anyFailed && (
        <p className="mt-1 text-xs text-amber-600 dark:text-amber-400">
          {[
            cogFailed && 'EEG signals',
            faceFailed && 'facial recognition signals',
            heartFailed && 'heart-rate signals',
            sessionsFailed && 'session counts',
          ].filter(Boolean).join(', ')} could not be loaded — the figures shown for them are not measurements.
        </p>
      )}
      {report?.truncated && (
        <p className="mt-1 text-xs text-amber-600 dark:text-amber-400">
          Showing the most recent samples only — earlier days in this range exceeded the retrieval limit.
          {unretrieved > 0 && ` ${unretrieved} ${unretrieved === 1 ? 'day is' : 'days are'} shown as a gap because the data could not be retrieved, not because there was no activity.`}
        </p>
      )}
    </div>
  )
}


/**
 * At-home practice strategies, with the control that generates them.
 * `signalsRetrieved === false` swaps the subtitle to "general suggestions".
 * `viewerRole` changes only the framing; the heading stays "At-Home" because
 * the advice is written for a parent. Unrecognised roles read as parent.
 */
export function StrategyPanel({ strategies, source, signalsRetrieved, loading, error, onGenerate,
                                viewerRole = 'parent' }) {
  const signalsMissing = signalsRetrieved === false
  const forTeacher = viewerRole === 'teacher'
  return (
    <div className="rounded-2xl border border-gray-100 dark:border-gray-800 bg-white dark:bg-gray-900 p-5 shadow-sm">
      <div className="flex items-start justify-between gap-3 flex-wrap mb-4">
        <div>
          <h3 className="font-black text-gray-900 dark:text-white flex items-center gap-2">
            <Sparkles size={18} className="text-violet-500" /> At-Home Learning Strategies
          </h3>
          <p className="text-xs text-gray-600 mt-1 dark:text-gray-400">
            {signalsMissing
              ? <>General practice suggestions — this week&apos;s signal data could not be read. Learning indicators only — not medical or behavioural advice.</>
              : forTeacher
                ? <>Practice suggestions built from this week&apos;s report, written for a family to use at home — share them rather than read them as classroom advice. Learning indicators only — not medical or behavioural advice.</>
                : <>Practice suggestions built from this week&apos;s report. Learning indicators only — not medical or behavioural advice.</>}
          </p>
        </div>
        <button
          type="button"
          onClick={onGenerate}
          disabled={loading}
          className="inline-flex items-center gap-2 px-4 py-2.5 rounded-xl bg-violet-600 hover:bg-violet-700 disabled:opacity-60 text-white text-sm font-bold shadow transition"
        >
          <Sparkles size={16} /> {loading ? 'Generating…' : 'Generate strategies'}
        </button>
      </div>

      {error ? (
        <p className="text-sm text-gray-500 dark:text-gray-400">{error}</p>
      ) : loading ? (
        <div className="space-y-2">{[1, 2, 3].map(i => <div key={i} className="h-12 rounded-xl bg-slate-50 dark:bg-gray-800 animate-pulse" />)}</div>
      ) : !strategies?.length ? (
        <p className="text-sm text-gray-600 dark:text-gray-400">No strategies generated yet.</p>
      ) : (
        <div className="space-y-3">
          {/* Above the list: it changes how the items should be read. */}
          {signalsMissing && (
            <p className="rounded-xl border border-amber-200 dark:border-amber-900/50 bg-amber-50 dark:bg-amber-900/20 px-3 py-2 text-xs text-amber-800 dark:text-amber-200">
              This week&apos;s signal data couldn&apos;t be loaded, so these are general suggestions rather than ones based on
              {forTeacher ? ' this student’s' : ' your child’s'} report. Try again shortly.
            </p>
          )}
          {/* Index key: replaced wholesale, never reordered, text not unique. */}
          {strategies.map((s, i) => (
            <div key={i} className="flex gap-3 rounded-xl bg-slate-50 dark:bg-gray-800 p-3">
              <span className="w-6 h-6 rounded-lg bg-violet-600 text-white flex items-center justify-center text-xs font-black shrink-0">{i + 1}</span>
              <p className="text-sm text-gray-700 dark:text-gray-200">{s}</p>
            </div>
          ))}
          {source && <p className="text-[11px] text-gray-600 dark:text-gray-400">Source: {source}</p>}
        </div>
      )}
    </div>
  )
}


/**
 * Plain sentences describing the charts, from `POST /api/students/{id}/chart-summary`.
 * On demand only (a model call per student otherwise). `retrieved` carries one
 * flag per backing read. `viewerRole` works as on `StrategyPanel`.
 */
export function ChartSummaryPanel({ summary, source, retrieved, loading, error, onGenerate,
                                    viewerRole = 'parent' }) {
  const forTeacher = viewerRole === 'teacher'
  // `=== false`, not falsy: absent on older payloads.
  const missing = [
    retrieved?.signals === false && 'this week’s signal averages',
    retrieved?.trend === false && 'the term trend',
    retrieved?.stats === false && 'the practice totals',
    retrieved?.topics === false && 'the topic figures',
  ].filter(Boolean)

  return (
    <div className="rounded-2xl border border-gray-100 dark:border-gray-800 bg-white dark:bg-gray-900 p-5 shadow-sm">
      <div className="flex items-start justify-between gap-3 flex-wrap mb-4">
        <div>
          <h3 className="font-black text-gray-900 dark:text-white flex items-center gap-2">
            <Activity size={18} className="text-sky-500" /> What These Charts Show
          </h3>
          <p className="text-xs text-gray-600 mt-1 dark:text-gray-400">
            {forTeacher
              ? <>A written read-out of the report above, for this student. Learning indicators only — not medical or behavioural advice.</>
              : <>A written read-out of the report above. Learning indicators only — not medical or behavioural advice.</>}
          </p>
        </div>
        <button
          type="button"
          onClick={onGenerate}
          disabled={loading}
          className="inline-flex items-center gap-2 px-4 py-2.5 rounded-xl bg-sky-600 hover:bg-sky-700 disabled:opacity-60 text-white text-sm font-bold shadow transition"
        >
          <Activity size={16} /> {loading ? 'Generating…' : 'Generate summary'}
        </button>
      </div>

      {error ? (
        <p className="text-sm text-gray-500 dark:text-gray-400">{error}</p>
      ) : loading ? (
        <div className="space-y-2">{[1, 2, 3].map(i => <div key={i} className="h-12 rounded-xl bg-slate-50 dark:bg-gray-800 animate-pulse" />)}</div>
      ) : !summary?.length ? (
        <p className="text-sm text-gray-600 dark:text-gray-400">No summary generated yet.</p>
      ) : (
        <div className="space-y-3">
          {/* Above the sentences: it changes how they should be read. */}
          {missing.length > 0 && (
            <p className="rounded-xl border border-amber-200 dark:border-amber-900/50 bg-amber-50 dark:bg-amber-900/20 px-3 py-2 text-xs text-amber-800 dark:text-amber-200">
              Part of this report couldn’t be loaded ({missing.join(', ')}), so the summary below describes
              less than the charts do. Try again shortly.
            </p>
          )}
          {/* Index key: replaced wholesale, never reordered, text not unique. */}
          {summary.map((s, i) => (
            <div key={i} className="flex gap-3 rounded-xl bg-slate-50 dark:bg-gray-800 p-3">
              <span className="w-6 h-6 rounded-lg bg-sky-600 text-white flex items-center justify-center text-xs font-black shrink-0">{i + 1}</span>
              <p className="text-sm text-gray-700 dark:text-gray-200">{s}</p>
            </div>
          ))}
          {source && <p className="text-[11px] text-gray-600 dark:text-gray-400">Source: {source}</p>}
        </div>
      )}
    </div>
  )
}
