import { useId } from 'react'
import { Activity, Brain, Heart, Leaf, Radio, Sparkles, Waves } from 'lucide-react'
import ScaleNote from './ScaleNote'
import MeasureTile from './MeasureTile'
import { combineScales } from '../../lib/scoreScale'
import { EMOTION_COLOURS, UNKNOWN_EMOTION_COLOUR } from '../../lib/emotions'
import {
  LineChart, Line, XAxis, YAxis, CartesianGrid,
  PieChart, Pie, Cell, Legend,
} from 'recharts'
import ChartTooltip from '../charts/ChartTooltip'
import { sliceSpec } from '../charts/describeSeries'
import AccessibleChart from '../charts/AccessibleChart'
import SeriesFilter from '../charts/SeriesFilter'
import { useSeriesFilter } from '../../hooks/useSeriesFilter'
import { calmPct, calmRatio, emotionOn, offLabel, pct, ratio, valueOrReason } from '../../lib/signalFormat'
import { AROUSAL_REASONS, MEASURES } from '../../lib/signalGlossary'

// muse_optics / muse_ppg / rppg are storage values, not display strings.
const SOURCE_LABELS = {
  muse_optics: 'Headband (optical)',
  muse_ppg: 'Headband (PPG)',
  rppg: 'Camera',
}
function sourceLabel(source) {
  return SOURCE_LABELS[source] || source
}

// Nulls stay null so a day with no data is a gap, not a line at zero.
function toPct(value) {
  const n = ratio(value)
  return n === null ? null : n * 100
}

// Absolute units (bpm, ms); never through `toPct`.
function unit(value, suffix, digits = 0) {
  const n = ratio(value)
  return n === null ? 'N/A' : `${n.toFixed(digits)}${suffix}`
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
    erasedAt: report?.emotion_erased_at,
  }
}

/**
 * The offLabel reason for the EEG channel.
 * `eeg_enabled`, not `eeg_included`: cognitive is always read. Absent reads as on. A failed
 * cognitive read (the weekly report's `retrieved`) is unknown, not "No sensor".
 */
function eegReason(report) {
  const on = report?.eeg_enabled !== false
  return {
    on,
    revokedAt: report?.eeg_revoked_at ?? null,
    // A known revocation comes from the consent read, so a failed EEG read doesn't hide it.
    consentRetrieved: report?.consent_retrieved === false
      || (on && report?.retrieved?.cognitive === false)
      ? false : report?.consent_retrieved,
    samples: report?.sample_counts?.cognitive,
    erasedAt: report?.eeg_erased_at,
  }
}

/** The offLabel reason for a shown heart tile: consented, so the read, samples and an erasure decide. */
function heartReason(report) {
  return {
    on: true,
    // A failed heart read is unknown, not "No sensor", as for EEG.
    consentRetrieved: report?.consent_retrieved === false || report?.retrieved?.heart === false
      ? false : report?.consent_retrieved,
    samples: report?.sample_counts?.heart,
    erasedAt: report?.heart_erased_at,
  }
}

/** Body arousal's tile value, or the backend `state`'s own words; never 0% for an absence. */
function arousalDisplay(arousal) {
  if (!arousal) return { value: null, reason: 'Not reported' }
  if (arousal.state === 'measured') return { value: pct(arousal.high_share), reason: "Couldn't load" }
  return { value: null, reason: AROUSAL_REASONS[arousal.state] ?? "Couldn't check" }
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
    <div role="group" aria-label={label}
         className="rounded-2xl border border-gray-100 dark:border-gray-800 bg-white dark:bg-gray-900 p-4 shadow-sm">
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

export function LiveSignalSummary({ report, title = 'Most recent readings' }) {
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
            The latest headband{heartShown ? ', heart' : ''} and camera readings.
          </p>
        </div>
        <Radio size={18} className="text-emerald-500" />
      </div>
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
        <MeasureTile measure="focus" value={pct(cog.focus)} reason={eegReason(report)}
                     icon={Brain} tone="emerald" />
        <MeasureTile measure="calm" value={calmPct(cog.stress)} reason={eegReason(report)}
                     icon={Leaf} tone="teal" />
        {/* No Engagement tile (it is focus) and no attention tile (no producer). */}
        {heartShown && (
          <MeasureTile measure="heart_rate_bpm" value={unit(heart.heart_rate_bpm, ' bpm')}
                       reason={heartReason(report)} icon={Heart} tone="rose" />
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

// One list per series: lines, table columns and toggles all derive from it (docs/frontend.md).
// Rows are scaled on the way in, so no `scale`. Colours are pinned by test_chart_render.py.
const SIGNAL_SERIES = [
  { key: 'focus', label: MEASURES.focus.name, unit: '%', colour: '#6366f1', group: 'pct', name: 'Focus' },
  // Dashed: focus and calm are close for tritan vision, so the line style also tells them apart.
  { key: 'calm', label: MEASURES.calm.name, unit: '%', colour: '#0d9488', group: 'pct', name: 'Calm', dash: '6 3' },
  { key: 'heart_rate_bpm', label: MEASURES.heart_rate_bpm.name, unit: ' bpm', colour: '#a855f7', group: 'bpm', name: 'Heart rate (bpm)' },
  { key: 'rmssd_ms', label: MEASURES.rmssd_ms.name, unit: ' ms', colour: '#f59e0b', group: 'ms', name: 'Heart-rate variability (ms)' },
  { key: 'body_arousal', label: MEASURES.body_arousal.name, unit: '%', colour: '#ea580c', group: 'arousal', name: 'Body arousal (%)',
    missing: r => AROUSAL_REASONS[r.body_arousal_state] ?? null },
]
const HEART_KEYS = new Set(['heart_rate_bpm', 'rmssd_ms', 'body_arousal'])

// One small chart per unit: never two y-axes on one chart.
const CHART_GROUPS = [
  { id: 'pct', title: 'Focus and calm', unit: '%', domain: [0, 100] },
  { id: 'bpm', title: 'Heart rate', unit: ' bpm', domain: ['auto', 'auto'] },
  { id: 'ms', title: 'Heart-rate variability', unit: ' ms', domain: ['auto', 'auto'] },
  { id: 'arousal', title: 'Body arousal', unit: '%', domain: [0, 100] },
]

/** A row with its signals scaled for drawing: ratios to percent, stress to calm, nulls kept. */
function chartRow(row, label) {
  return {
    ...row,
    label,
    focus: toPct(row.focus),
    calm: toPct(calmRatio(row.stress)),
    heart_rate_bpm: ratio(row.heart_rate_bpm),
    rmssd_ms: ratio(row.rmssd_ms),
    body_arousal: toPct(row.body_arousal),
  }
}

/**
 * Stacked small charts sharing one x axis and one set of toggles.
 * A group with no shown series is not mounted, so it leaves no empty axis or column-less table.
 */
function SignalCharts({ rows, heartShown, rowLabel, period, filterLabel }) {
  const syncId = useId()
  const series = SIGNAL_SERIES.filter(s => heartShown || !HEART_KEYS.has(s.key))
  const { hidden, toggle, showAll, shownOf } = useSeriesFilter()
  const shown = shownOf(series)
  return (
    <div>
      <SeriesFilter series={series} hidden={hidden} onToggle={toggle} label={filterLabel} />
      {shown.length === 0 ? (
        /* Every series hidden: a claim about the view, not the data. */
        <div className="h-40 rounded-2xl bg-slate-50 dark:bg-gray-800 p-3 flex flex-col items-center justify-center gap-3">
          <p className="text-sm text-gray-600 dark:text-gray-400">No measurements selected.</p>
          <button type="button" onClick={showAll}
                  className="px-3 py-2.5 min-h-[44px] rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-900 text-xs font-bold text-gray-900 dark:text-white hover:bg-slate-50 dark:hover:bg-gray-800 transition">
            Show all
          </button>
        </div>
      ) : (
        <div className="space-y-3">
          {CHART_GROUPS.map(g => {
            const lines = shown.filter(s => s.group === g.id)
            if (lines.length === 0) return null
            return (
              <div key={g.id} className="rounded-2xl bg-slate-50 dark:bg-gray-800 p-3">
                <p className="text-xs font-bold text-gray-600 dark:text-gray-400 mb-1">{g.title}</p>
                <AccessibleChart className="h-40"
                  headline={`${g.title} ${period} over ${rows.length} ${rowLabel.toLowerCase()}${rows.length === 1 ? '' : 's'}.`}
                  rows={rows} rowKey="label" rowLabel={rowLabel}
                  columns={lines.map(({ key, label, unit: u, missing }) => ({ key, label, unit: u, missing }))}>
                  <LineChart data={rows} syncId={syncId} margin={{ top: 8, right: 10, left: -12, bottom: 0 }}>
                    <CartesianGrid strokeDasharray="3 3" opacity={0.25} />
                    <XAxis dataKey="label" fontSize={11} tickLine={false} />
                    <YAxis domain={g.domain} fontSize={11} tickLine={false} unit={g.unit} />
                    <ChartTooltip />
                    {lines.length > 1 && <Legend />}
                    {/* Dots, so a single recorded point is still visible. */}
                    {lines.map(s => (
                      <Line key={s.key} type="monotone" dataKey={s.key} stroke={s.colour}
                            strokeWidth={2} strokeDasharray={s.dash} dot={{ r: 3 }}
                            name={s.name} connectNulls={false} />
                    ))}
                  </LineChart>
                </AccessibleChart>
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}

/**
 * Week-over-week averages from `signal_daily_rollup`, which outlives the raw rows.
 * A failed read is not a quiet term; an empty week is a gap, not a missing bar.
 */
export function SignalTrend({ trend, title = 'Week by week' }) {
  const weeks = trend?.weeks || []
  const failed = trend?.retrieved === false
  // The Monday, as `MM-DD`.
  const rows = weeks.map(w => chartRow(w, w.week_start ? w.week_start.slice(5) : ''))
  const recorded = weeks.filter(w => w.days_with_data > 0).length

  return (
    <div className="rounded-2xl border border-gray-100 dark:border-gray-800 bg-white dark:bg-gray-900 p-5 shadow-sm">
      {/* Says where a score-scale change falls in the term; otherwise nothing. */}
      <ScaleNote scale={combineScales(weeks)} what="The lines below" />
      <div className="mb-4">
        <h3 className="font-black text-gray-900 dark:text-white">{title}</h3>
        <p className="text-xs text-gray-600 dark:text-gray-400">
          Weekly averages, weighted by how much was recorded each day. Weeks with nothing
          recorded are left as gaps{weeks.length ? `; ${recorded} of ${weeks.length} have readings` : ''}.
        </p>
      </div>
      {failed || rows.length === 0 ? (
        <div className="h-40 rounded-2xl bg-slate-50 dark:bg-gray-800 p-3 flex items-center justify-center text-sm text-gray-600 text-center px-4 dark:text-gray-400">
          {failed ? 'The week-by-week readings could not be loaded.' : 'No readings yet.'}
        </div>
      ) : (
        <SignalCharts rows={rows} heartShown={heartOn(trend)} rowLabel="Week of"
                      period="week by week" filterLabel="Measurements shown week by week" />
      )}
    </div>
  )
}

/** A small amber line under the tile it qualifies; amber marks "this figure is not complete". */
function Caveat({ children }) {
  return <p className="mt-1 text-xs text-amber-700 dark:text-amber-400">{children}</p>
}

export function WeeklySignalReport({ report, title = 'This week' }) {
  const avg = report?.averages || {}
  const highlights = report?.highlights || {}
  const counts = report?.sample_counts || {}
  const usual = report?.usual?.measures
  const faceOn = emotionOn(report)
  const heartShown = heartOn(report)
  const rows = (report?.daily || []).map(d => chartRow(d, d.date ? d.date.slice(5) : ''))
  // Signal days that could not be read draw as gaps like quiet days, so say so.
  // Not `sessions_retrieved`: per-day session counts are not drawn here.
  const unretrieved = (report?.daily || []).filter(
    d => d.cognitive_retrieved === false
      || d.face_retrieved === false
      || d.heart_retrieved === false
  ).length
  // `=== false`, not falsy: undefined is an older payload, null the facial opt-out.
  const retrieved = report?.retrieved || {}
  const cogFailed = retrieved.cognitive === false
  const faceFailed = retrieved.face === false
  const sessionsFailed = retrieved.sessions === false
  // Under `truncated`, a count that came back exceeds the rows read; equal means it did not.
  const sessionsExact = (report?.sessions_recorded ?? 0) > (counts.sessions ?? 0)
  const heartFailed = retrieved.heart === false
  const anyFailed = cogFailed || faceFailed || heartFailed || sessionsFailed
  // Consent unreadable: "we couldn't find out", not "declined".
  const consentFailed = report?.consent_retrieved === false
  // Tells "measured but unusable" from "never measured".
  const heartSamples = counts.heart || 0
  const arousal = arousalDisplay(report?.body_arousal)
  const emotionSlices = Object.entries(report?.emotion_distribution || {})
    .map(([name, value]) => ({ name, value }))
    .sort((a, b) => b.value - a.value)

  return (
    <div className="rounded-2xl border border-gray-100 dark:border-gray-800 bg-white dark:bg-gray-900 p-5 shadow-sm">
      <div className="mb-4">
        <h3 className="font-black text-gray-900 dark:text-white">{title}</h3>
        <p className="text-xs text-gray-600 dark:text-gray-400">
          Averages over the last {report?.days || 7} days, each compared with this student&apos;s own
          earlier weeks.
        </p>
        {/* Above everything: it changes how every figure below is read. */}
        {consentFailed && (
          <p className="mt-1 text-xs text-amber-700 dark:text-amber-400">
            Consent settings could not be read, so heart and facial data were left out of this report — that is not a record of what was permitted.
          </p>
        )}
        {/* An average can mix both score scales with no visible step. */}
        <ScaleNote scale={avg.score_scale} what="The averages below" />
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3 mb-5">
        <MeasureTile measure="focus" value={pct(avg.focus)} reason={eegReason(report)}
                     usual={usual?.focus} icon={Brain} tone="emerald">
          {cogFailed && <Caveat>Headband readings could not be loaded.</Caveat>}
        </MeasureTile>
        <MeasureTile measure="calm" value={calmPct(avg.stress)} reason={eegReason(report)}
                     usual={usual?.calm} icon={Leaf} tone="teal">
          {cogFailed && <Caveat>Headband readings could not be loaded.</Caveat>}
        </MeasureTile>
        {heartShown && (
          <>
            <MeasureTile measure="heart_rate_bpm" value={unit(highlights.heart_rate_bpm, ' bpm')}
                         reason={heartReason(report)} usual={usual?.heart_rate_bpm}
                         icon={Heart} tone="rose">
              {heartSamples > 0 && ratio(highlights.heart_rate_bpm) === null && (
                <Caveat>Heart readings were recorded but none met the quality threshold.</Caveat>
              )}
              {heartFailed && <Caveat>Heart readings could not be loaded.</Caveat>}
            </MeasureTile>
            <MeasureTile measure="rmssd_ms" value={unit(highlights.rmssd_ms, ' ms')}
                         reason={heartReason(report)} usual={usual?.rmssd_ms}
                         icon={Waves} tone="amber" />
            <MeasureTile measure="body_arousal" value={arousal.value} reason={arousal.reason}
                         usual={usual?.body_arousal} icon={Activity} tone="rose">
              {report?.body_arousal?.few_readings && (
                <Caveat>Based on only a few readings, so treat it as rough.</Caveat>
              )}
              {report?.body_arousal?.pending === true && (
                <Caveat>A lesson still in progress is not counted yet.</Caveat>
              )}
            </MeasureTile>
          </>
        )}
        <div>
          {/* sessions_recorded: sample_counts.sessions is capped. Dash on a failed read, never 0. */}
          <MiniMetric label="Sessions"
                      value={sessionsFailed ? '—' : (report?.sessions_recorded ?? counts.sessions ?? 0)}
                      icon={Radio} tone="amber" />
          {sessionsFailed && <Caveat>Session counts could not be loaded.</Caveat>}
          {/* `truncated` is the sessions read alone; the signal figures are whole-week aggregates. */}
          {report?.truncated && (
            <Caveat>
              {sessionsExact
                ? 'Sessions reached the retrieval limit; this total is still the full count.'
                : 'Sessions reached the retrieval limit, so this total counts only the sessions read.'}
            </Caveat>
          )}
        </div>
      </div>

      {rows.length === 0 ? (
        <div className="h-40 rounded-2xl bg-slate-50 dark:bg-gray-800 p-3 flex items-center justify-center text-sm text-gray-600 text-center px-4 dark:text-gray-400">
          {anyFailed ? "This week's readings could not be loaded." : 'No readings this week yet.'}
        </div>
      ) : (
        <SignalCharts rows={rows} heartShown={heartShown} rowLabel="Day"
                      period="day by day" filterLabel="Measurements shown day by day" />
      )}
      {/* Its own line: an unread signal day is unrelated to `truncated`. */}
      {unretrieved > 0 && (
        <Caveat>
          {`${unretrieved} ${unretrieved === 1 ? 'day is' : 'days are'} shown as a gap because the data could not be retrieved, not because there was no activity.`}
        </Caveat>
      )}

      <div className="mt-4 grid md:grid-cols-3 gap-3 text-sm">
        <div className="rounded-xl bg-slate-50 dark:bg-gray-800 p-3">
          <p className="text-xs font-bold uppercase tracking-widest text-gray-600 dark:text-gray-400">Lowest calm reading</p>
          {/* The highest stress reading is the lowest calm one, the right way up. */}
          <p className="font-bold text-gray-900 dark:text-white">{valueOrReason(calmPct(highlights.highest_stress), eegReason(report))}</p>
        </div>
        <div className="rounded-xl bg-slate-50 dark:bg-gray-800 p-3">
          <p className="text-xs font-bold uppercase tracking-widest text-gray-600 dark:text-gray-400">Lowest focus reading</p>
          <p className="font-bold text-gray-900 dark:text-white">{valueOrReason(pct(highlights.lowest_focus), eegReason(report))}</p>
        </div>
        <div className="rounded-xl bg-slate-50 dark:bg-gray-800 p-3">
          <p className="text-xs font-bold uppercase tracking-widest text-gray-600 dark:text-gray-400">Dominant Emotion</p>
          <p className="font-bold text-gray-900 dark:text-white capitalize">
            {valueOrReason(faceOn && highlights.dominant_emotion, faceReason(report, faceOn))}
          </p>
          {faceFailed && <Caveat>Facial readings could not be loaded.</Caveat>}
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
        <div className="mt-3 rounded-xl bg-slate-50 dark:bg-gray-800 p-3 text-sm">
          <p className="text-xs font-bold uppercase tracking-widest text-gray-600 dark:text-gray-400">Heart sensor</p>
          {/* Named: accuracy differs by source, and the camera is unvalidated. */}
          <p className="font-bold text-gray-900 dark:text-white">
            {(report?.heart_sources || []).length
              ? report.heart_sources.map(sourceLabel).join(', ')
              : offLabel(heartReason(report))}
          </p>
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
                      <Cell key={slice.name} fill={EMOTION_COLOURS[slice.name] || UNKNOWN_EMOTION_COLOUR} />
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
 * On demand only (a model call per student otherwise). `retrieved` carries one flag per
 * backing read. `viewerRole` works as on `StrategyPanel`. `hideSensorLines` drops the
 * `sensorLines` indexes ("Hide sensor data"), which the backend keeps in baseline order.
 */
export function ChartSummaryPanel({ summary, source, retrieved, loading, error, onGenerate,
                                    viewerRole = 'parent', sensorLines = [],
                                    hideSensorLines = false }) {
  const forTeacher = viewerRole === 'teacher'
  // `=== false`, not falsy: absent on older payloads.
  const missing = [
    retrieved?.signals === false && 'this week’s signal averages',
    retrieved?.trend === false && 'the term trend',
    retrieved?.stats === false && 'the practice totals',
    retrieved?.topics === false && 'the topic figures',
  ].filter(Boolean)
  const lines = (summary || []).map((text, i) => ({ text, i }))
    .filter(({ i }) => !(hideSensorLines && sensorLines.includes(i)))

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
          {lines.map(({ text, i }, n) => (
            <div key={i} className="flex gap-3 rounded-xl bg-slate-50 dark:bg-gray-800 p-3">
              <span className="w-6 h-6 rounded-lg bg-sky-600 text-white flex items-center justify-center text-xs font-black shrink-0">{n + 1}</span>
              <p className="text-sm text-gray-700 dark:text-gray-200">{text}</p>
            </div>
          ))}
          {source && <p className="text-[11px] text-gray-600 dark:text-gray-400">Source: {source}</p>}
        </div>
      )}
    </div>
  )
}
