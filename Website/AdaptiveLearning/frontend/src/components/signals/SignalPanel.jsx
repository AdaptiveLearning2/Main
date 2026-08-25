import { Activity, Brain, Heart, Radio, Sparkles, Zap } from 'lucide-react'
import {
  LineChart, Line, XAxis, YAxis, Tooltip, CartesianGrid,
  PieChart, Pie, Cell, Legend,
} from 'recharts'
import { sliceSpec } from '../charts/describeSeries'
import AccessibleChart from '../charts/AccessibleChart'

// One string cannot answer for three channels, so this is a function of
// *which* channel and *why* it has no value: withdrawn, sensor absent, or
// read failed. Never render "no data" for something that was never recorded
// -- "N/A" for a withdrawn channel and "Off" for a failed read both make a
// claim that isn't true.
const CHANNEL_STATE = {
  // Consent off, and we know when. `since` is the date it was switched off.
  revoked: since => (since ? `Off since ${since}` : 'Not recorded'),
  // The consent read itself failed -- distinct from revoked, since we can't
  // claim the student turned it off when we simply don't know.
  unknown: () => 'Unavailable',
  // Consented and present, but this window produced nothing usable.
  calibrating: () => 'Calibrating',
  // Consented, but no sensor produced anything this period.
  noSensor: () => 'No sensor',
}

// Formats an ISO timestamp as a short date, or null when there isn't one, so
// a caller can tell "off, and here's when" from "off, unknown when" rather
// than printing "Invalid Date".
function shortDate(iso) {
  if (!iso) return null
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? null : d.toLocaleDateString(undefined,
    { day: 'numeric', month: 'short' })
}

/**
 * What to show in a tile for a channel that has no value.
 *
 * `on` is whether the channel was read at all, `revokedAt` when it was switched
 * off, `consentRetrieved` whether we could find out, and `samples` how many
 * readings the period produced.
 */
export function offLabel({ on, revokedAt, consentRetrieved, samples }) {
  if (consentRetrieved === false) return CHANNEL_STATE.unknown()
  if (!on) return CHANNEL_STATE.revoked(shortDate(revokedAt))
  // `samples > 0` means readings arrived and none was usable -- calibrating,
  // not an absent sensor.
  return samples > 0 ? CHANNEL_STATE.calibrating() : CHANNEL_STATE.noSensor()
}

/**
 * A rendered value, or the reason there isn't one.
 *
 * Every tile goes through here rather than branching on the channel flag
 * directly -- branching only on "is the channel on" left `pct()`'s raw 'N/A'
 * showing whenever a consented channel produced nothing usable.
 */
export function valueOrReason(value, reason) {
  return (value && value !== 'N/A') ? value : offLabel(reason)
}

// One colour per FER+ label, fixed rather than positional -- a palette by
// slice order would recolour every emotion whenever the distribution
// changed, so the same week viewed twice would look like different data.
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

// Signal values cross the wire as 0..1 ratios, matching what /live and the
// parent dashboard both assume. Rendering without scaling prints focus 0.72
// as "1%" -- every metric ~100x too small.
//
// A measurement, or null for anything that is not one. `Number.isNaN` alone
// isn't enough: Number('') and Number('  ') are both 0, so an empty value
// would render as a confident "0%", and Number.isNaN(Infinity) is false, so
// a non-finite number would pass through. Number.isFinite covers both.
function ratio(value) {
  if (value === null || value === undefined) return null
  if (typeof value === 'string' && value.trim() === '') return null
  const n = Number(value)
  return Number.isFinite(n) ? n : null
}

// Exported so the parent dashboard, which renders the same 0..1 ratios,
// shares one definition of what an unrenderable value looks like.
export function pct(value) {
  const n = ratio(value)
  return n === null ? 'N/A' : `${Math.round(n * 100)}%`
}

// Same scaling for chart series. Nulls must stay null, not become 0, so a
// day with no data leaves a gap rather than drawing a line at zero.
function toPct(value) {
  const n = ratio(value)
  return n === null ? null : n * 100
}

// Absolute units, not ratios. `toPct` must never touch these -- a 72 bpm day
// through it draws at 7200%, so heart rate gets its own axis.
function unit(value, suffix, digits = 0) {
  const n = ratio(value)
  return n === null ? 'N/A' : `${n.toFixed(digits)}${suffix}`
}

// Two channels are consented separately, so one flag can't answer for both.
// `face_included` is a deprecated alias for the emotion channel, kept as a
// fallback so a payload from before the split still renders correctly.
export function emotionOn(report) {
  if (report?.emotion_included !== undefined) return report.emotion_included !== false
  // The legacy alias. Absent means a payload from before the split, and
  // treating that as "excluded" would blank a channel that was recorded.
  return report?.face_included !== false
}

function heartOn(report) {
  // Absent means a pre-split payload with no heart data -- defaulting to true
  // would draw an empty series and claim the sensor recorded nothing.
  return report?.heart_included === true
}

// The offLabel `reason` for every face-derived tile on this panel -- one
// definition so a change to the backing fields updates every call site.
function faceReason(report, faceOn) {
  return {
    on: faceOn,
    revokedAt: report?.emotion_revoked_at,
    consentRetrieved: report?.consent_retrieved,
    samples: report?.sample_counts?.face,
  }
}

/** The same four states for the EEG channel.
 *
 * A headband with poor electrode contact writes rows with the measurement
 * columns nulled, and without this the snapshot printed "N/A" beside a
 * weekly average of 64% -- two true numbers that looked like a contradiction.
 *
 * `eeg_enabled`, not `eeg_included` like the other two channels: the
 * cognitive channel has no opt-out on the aggregate and is always read, and
 * withdrawal deliberately keeps what was already recorded -- so a withdrawn
 * EEG channel can still hold true averages from before the withdrawal.
 *
 * Absent means a payload from before the field existed and reads as *on* --
 * defaulting to off would claim a headband was switched off when it wasn't.
 * Same fallback as `emotionOn`; opposite of `heartOn`, where absent genuinely
 * meant the channel didn't exist yet.
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
  // `heart` is absent from `latest` when the channel wasn't read -- the
  // backend omits the key rather than sending null, since a tile rendered
  // from `{}` would read as a sensor recording nothing.
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
        {/* Through `valueOrReason`, like every other tile -- these three used
            to print `pct()`'s raw 'N/A' beside a weekly average of 64%. */}
        <MiniMetric label="Focus" value={valueOrReason(pct(cog.focus), eegReason(report))}
                    icon={Brain} tone="emerald" />
        <MiniMetric label="Stress" value={valueOrReason(pct(cog.stress), eegReason(report))}
                    icon={Zap} tone="rose" />
        <MiniMetric label="Engagement" value={valueOrReason(pct(cog.engagement), eegReason(report))}
                    icon={Activity} tone="indigo" />
        {/* No attention tile. `face_signals.attention` has no producer, so
            the tile could only ever say "Calibrating" -- reads as warming up
            rather than a measurement that will never arrive. Blocked on a
            labelled reference: attention inferred from head direction is
            least valid for this product's users and renders as an
            objective-looking percentage. */}
        {/* bpm, not a percentage -- `unit()` never touches the 0..1 path. */}
        {heartShown && (
          <MiniMetric label="Heart Rate" value={unit(heart.heart_rate_bpm, ' bpm')} icon={Heart} tone="rose" />
        )}
      </div>
      {/* One tile, not two. "Identity Confidence" was retired: face identity
          never had a producer, and identifying a child by face is a
          different purpose from what camera consent asks about. */}
      <div className="mt-4 grid gap-3 text-sm">
        <div className="rounded-xl bg-slate-50 dark:bg-gray-800 p-3">
          <p className="text-xs font-bold uppercase tracking-widest text-gray-600 dark:text-gray-400">Facial Emotion</p>
          {/* Not a binary faceOn ? value : "Reporting off" -- a failed consent
              read also leaves faceOn false, and offLabel tells that apart
              from a genuine withdrawal. */}
          <p className="font-bold text-gray-900 dark:text-white capitalize">
            {valueOrReason(faceOn && face.emotion, faceReason(report, faceOn))}
          </p>
        </div>
      </div>
    </div>
  )
}

/**
 * Week-over-week averages, over months rather than days.
 *
 * A different question from the panel below it, and a different source. The
 * weekly report reads the per-sample tables under a row cap; this reads
 * `signal_daily_rollup`, which is the only copy that outlives
 * `expire_signal_rows` — and a trend is the surface most likely to be read
 * *after* a school year ends, which is exactly when the raw rows are gone.
 *
 * Same three-state rule as everything else here: a failed read is not a quiet
 * term, and a week with nothing recorded is a gap rather than a missing bar.
 */
export function SignalTrend({ trend, title = 'Term Trend' }) {
  const heartShown = heartOn(trend)
  const weeks = trend?.weeks || []
  const failed = trend?.retrieved === false

  const chartData = weeks.map(w => ({
    ...w,
    focus: toPct(w.focus),
    stress: toPct(w.stress),
    // bpm, not a ratio. Through `toPct` a 72 bpm week draws at 7200%.
    heart_rate_bpm: ratio(w.heart_rate_bpm),
    // The Monday, as `MM-DD`. The year is the same across any range this
    // component offers, so it would be repetition on every tick.
    label: w.week_start ? w.week_start.slice(5) : '',
  }))

  // Mirrors the drawn series and nothing else — the sr-only table is the text
  // alternative for *this picture*, so a column naming something no sighted
  // reader can see is a different report, not an equivalent one. `focus` and
  // `stress` are scaled on the way in above, hence no `scale` here; heart rate
  // is left in bpm and says so in its unit.
  const COLUMNS = [
    { key: 'focus',  label: 'Focus',  unit: '%' },
    { key: 'stress', label: 'Stress', unit: '%' },
    ...(heartShown ? [{ key: 'heart_rate_bpm', label: 'Heart rate', unit: ' bpm' }] : []),
  ]

  // Coverage belongs in the sentence, not in a column, for the reason above.
  // It has to be said somewhere: the rollup keeps per-day counts precisely so a
  // thin week stays visibly thin, and a week of three samples plotted beside a
  // week of four thousand looks equally solid.
  const recorded = weeks.filter(w => w.days_with_data > 0).length
  const headline = `Weekly signal averages across ${weeks.length} week`
    + `${weeks.length === 1 ? '' : 's'}, with data recorded on ${recorded} of them.`

  return (
    <div className="rounded-2xl border border-gray-100 dark:border-gray-800 bg-white dark:bg-gray-900 p-5 shadow-sm">
      <div className="mb-4">
        <h3 className="font-black text-gray-900 dark:text-white">{title}</h3>
        <p className="text-xs text-gray-600 dark:text-gray-400">
          Weekly averages, weighted by how much was recorded each day. Weeks
          with nothing recorded are left as gaps.
        </p>
      </div>

      <div className="h-56 rounded-2xl bg-slate-50 dark:bg-gray-800 p-3">
        {failed || chartData.length === 0 ? (
          <div className="h-full flex items-center justify-center text-sm text-gray-600 text-center px-4 dark:text-gray-400">
            {/* A failed read empties the series exactly as an untouched term
                does, so the "yet" claim is gated on having looked. */}
            {failed
              ? 'The term trend could not be loaded.'
              : 'No signal history yet.'}
          </div>
        ) : (
          <AccessibleChart headline={headline} rows={chartData}
                           rowKey="label" rowLabel="Week of" columns={COLUMNS}>
            <LineChart data={chartData} margin={{ top: 10, right: 10, left: -20, bottom: 0 }}>
              <CartesianGrid strokeDasharray="3 3" opacity={0.25} />
              <XAxis dataKey="label" fontSize={11} tickLine={false} />
              {/* Two axes and explicit ids, same as the daily chart: adding a
                  second axis without giving the first one an id silently binds
                  every existing series to the new one. */}
              <YAxis yAxisId="pct" domain={[0, 100]} fontSize={11} tickLine={false} />
              {heartShown && (
                <YAxis yAxisId="bpm" orientation="right" domain={['auto', 'auto']}
                       fontSize={11} tickLine={false} unit=" bpm" />
              )}
              <Tooltip />
              {/* Dots for the same reason as the daily chart: a student with
                  one recorded week gives every series a single point, which
                  draws no segment and renders as an empty chart without one. */}
              <Line yAxisId="pct" type="monotone" dataKey="focus" stroke="#6366f1" strokeWidth={2} dot={{ r: 3 }} name="Focus" connectNulls={false} />
              <Line yAxisId="pct" type="monotone" dataKey="stress" stroke="#f43f5e" strokeWidth={2} dot={{ r: 3 }} name="Stress" connectNulls={false} />
              {/* Omitted rather than drawn as an all-null line: an empty legend
                  entry reads as a measurement that flatlined. */}
              {heartShown && <Line yAxisId="bpm" type="monotone" dataKey="heart_rate_bpm" stroke="#a855f7" strokeWidth={2} dot={{ r: 3 }} name="Heart Rate (bpm)" connectNulls={false} />}
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
  // Scaled to percent to match the YAxis domain below -- left as 0..1
  // ratios, every series drew flat along the axis floor.
  const chartData = (report?.daily || []).map(d => ({
    ...d,
    focus: toPct(d.focus),
    stress: toPct(d.stress),
    // Not through toPct: these are bpm/ms, and scaling by 100 would flatten
    // every other series against the floor. `ratio()` only sanitises -- null
    // stays null so a day with no reading leaves a gap, not a zero.
    heart_rate_bpm: ratio(d.heart_rate_bpm),
    label: d.date ? d.date.slice(5) : '',
  }))
  // Days the row cap kept us from retrieving, vs. days with no activity --
  // both render as a gap, so the difference has to be stated separately.
  const unretrieved = (report?.daily || []).filter(
    d => d.cognitive_retrieved === false
      || d.face_retrieved === false
      || d.heart_retrieved === false
      || d.sessions_retrieved === false
  ).length
  // Which of the report's three reads happened. A failed query returns the
  // same empty rows as a quiet week, so this says which is which. `=== false`
  // throughout: undefined means a pre-field payload from a working read, and
  // null is the facial opt-out rather than a failure.
  const retrieved = report?.retrieved || {}
  const cogFailed = retrieved.cognitive === false
  const faceFailed = retrieved.face === false
  const sessionsFailed = retrieved.sessions === false
  const heartFailed = retrieved.heart === false
  const anyFailed = cogFailed || faceFailed || heartFailed || sessionsFailed
  // Separate from the above: the consent lookup itself failing, which means
  // "we couldn't find out", not "the student declined".
  const consentFailed = report?.consent_retrieved === false
  // So "measured but unusable" is distinguishable from "never measured" -- a
  // null average beside a nonzero count would otherwise look like off.
  const heartSamples = counts.heart || 0
  // Sorted by count for the legend, built from the backend's own tally
  // rather than recounting raw rows the frontend doesn't have.
  const emotionSlices = Object.entries(report?.emotion_distribution || {})
    .map(([name, value]) => ({ name, value }))
    .sort((a, b) => b.value - a.value)

  // **The columns are what the chart draws, and nothing else.** This is the
  // text alternative for *this picture*, so a series the picture omits does not
  // belong in it — a screen-reader user reading a series no sighted reader can
  // see is not equivalence, it is a different report.
  //
  // `engagement` was here, and was both undrawn and wrong. `chartData` scales
  // `focus` and `stress` through `toPct` and carries every other field across
  // by spread, so engagement stayed a 0..1 ratio and announced as "Engagement
  // 0% to 1%". The comment that stood here claimed `chartData` is "already
  // scaled to percentages" — true of the two fields above it, false of the one
  // it was being used to justify. Nothing on screen could contradict it,
  // because engagement is not plotted: the error existed *only* on the surface
  // this component was built to fix, which is the failure mode to expect here
  // and to check for deliberately.
  //
  // Heart rides on `heartShown` for the same reason: the line is conditional,
  // so the description of it has to be.
  //
  // No `scale` on the two that remain — unlike SessionReview and Live, these
  // really are scaled on the way in, and each is named in the map above.
  const TREND_COLUMNS = [
    { key: 'focus',  label: 'Focus',  unit: '%' },
    { key: 'stress', label: 'Stress', unit: '%' },
    ...(heartShown ? [{ key: 'heart_rate_bpm', label: 'Heart rate', unit: ' bpm' }] : []),
  ]

  return (
    <div className="rounded-2xl border border-gray-100 dark:border-gray-800 bg-white dark:bg-gray-900 p-5 shadow-sm">
      <div className="mb-4">
        <h3 className="font-black text-gray-900 dark:text-white">{title}</h3>
        <p className="text-xs text-gray-600 dark:text-gray-400">Averages are based on the last {report?.days || 7} days of available samples.</p>
      </div>

      <div className="grid grid-cols-2 lg:grid-cols-5 gap-3 mb-5">
        <MiniMetric label="Avg Focus" value={pct(avg.focus)} icon={Brain} tone="emerald" />
        <MiniMetric label="Avg Stress" value={pct(avg.stress)} icon={Zap} tone="rose" />
        <MiniMetric label="Engagement" value={pct(avg.engagement)} icon={Activity} tone="indigo" />
        {/* sessions_recorded, not sample_counts.sessions -- the latter is rows
            under the session row cap, so a heavy week showed the cap value
            instead of the real count. Falls back for older payloads.

            A dash when the sessions read failed, not the 0 the fallback would
            otherwise reach -- that would answer a broken query with a
            confident "0 sessions this week". */}
        <MiniMetric
          label="Sessions"
          value={sessionsFailed ? '—' : (report?.sessions_recorded ?? counts.sessions ?? 0)}
          icon={Radio}
          tone="amber"
        />
      </div>

      <div className="h-56 rounded-2xl bg-slate-50 dark:bg-gray-800 p-3">
        {chartData.length === 0 ? (
          <div className="h-full flex items-center justify-center text-sm text-gray-600 text-center px-4 dark:text-gray-400">
            {/* A failed read empties the series exactly like a quiet week
                does, so the "yet" claim has to be gated on having looked. */}
            {anyFailed
              ? 'Weekly signal data could not be loaded.'
              : 'No weekly signal data available yet.'}
          </div>
        ) : (
          // `role="img"` + a summary, because Recharts emits bare `<svg>`: with
          // no name and no walkable structure, the whole trend announced as
          // nothing. The `sr-only` table below carries the days themselves --
          // the summary alone is a headline with the data thrown away.
          <AccessibleChart
            headline={`Daily signal trend over ${chartData.length} day${chartData.length === 1 ? '' : 's'}.`}
            rows={chartData} rowKey="label" rowLabel="Day"
            columns={TREND_COLUMNS}>
            <LineChart data={chartData} margin={{ top: 10, right: 10, left: -20, bottom: 0 }}>
                <CartesianGrid strokeDasharray="3 3" opacity={0.25} />
                <XAxis dataKey="label" fontSize={11} tickLine={false} />
                {/* Two axes, because the series are in different units. The left
                    one is percent for the 0..1 ratios; heart rate is beats per
                    minute and would either flatten everything else against the
                    floor or, if scaled to match, be drawn at 7200%. Explicit ids
                    on both -- adding a second axis without giving the first one
                    an id silently binds every existing series to the new axis. */}
                <YAxis yAxisId="pct" domain={[0, 100]} fontSize={11} tickLine={false} />
                {heartShown && (
                  <YAxis yAxisId="bpm" orientation="right" domain={['auto', 'auto']}
                         fontSize={11} tickLine={false} unit=" bpm" />
                )}
                <Tooltip />
                {/* Distinct colours per series -- all three were "currentColor",
                    which rendered them identically and made the chart unreadable.
                    Matches the MiniMetric tones above. */}
                {/* #6366f1, matching SessionReview.jsx. It was #10b981 here,
                    which is the colour that file uses for *engagement* -- so one
                    green line meant focus on this panel and engagement on session
                    review, and a parent reading both was shown one colour for two
                    things. The archived SVGs re-render the session charts, so
                    those are the reference and this is the side that moved. */}
                {/* Dots, not `dot={false}`. This chart holds at most seven
                    points, one per day, and a student who practised on a single
                    day gives every series exactly one -- which draws no segment
                    and, without a dot, renders as a completely empty chart. That
                    is the normal case for a new student in their first week, so
                    the graph was blank precisely when it was first looked at. */}
                <Line yAxisId="pct" type="monotone" dataKey="focus" stroke="#6366f1" strokeWidth={2} dot={{ r: 3 }} name="Focus" />
                <Line yAxisId="pct" type="monotone" dataKey="stress" stroke="#f43f5e" strokeWidth={2} dot={{ r: 3 }} name="Stress" />
                {/* Omitted entirely with facial reporting off, rather than drawn
                    as an all-null series -- an empty legend entry reads as a
                    measurement that flatlined. */}
                {/* Same reasoning as the facial series: omitted rather than drawn
                    as an all-null line, because an empty legend entry reads as a
                    measurement that flatlined. */}
                {heartShown && <Line yAxisId="bpm" type="monotone" dataKey="heart_rate_bpm" stroke="#a855f7" strokeWidth={2} dot={{ r: 3 }} name="Heart Rate (bpm)" />}
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
          {/* Not a binary faceOn ? value : "Reporting off" -- a failed consent
              read also leaves faceOn false, and offLabel tells that apart
              from an actual withdrawal. */}
          <p className="font-bold text-gray-900 dark:text-white capitalize">
            {valueOrReason(faceOn && highlights.dominant_emotion, faceReason(report, faceOn))}
          </p>
        </div>
      </div>

      {/* Three states, and the middle one is the point.
          - Read: values.
          - Explicitly not read (`heart_included === false`): the row stays
            with *why* in place of numbers, since a channel that's off must
            say so and never read as "no data".
          - Field absent: a pre-split payload with no heart data to describe,
            so the row is omitted. */}
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
            {/* Not a raw "N/A": the channel was read (heartShown, above), so
                an empty average means unusable samples, not never asked. */}
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
            {/* Named because accuracy differs materially by source, and the
                camera one is unvalidated -- a reader comparing weeks should
                be able to see the sensor changed. */}
            <p className="font-bold text-gray-900 dark:text-white">
              {/* Not "N/A": the channel was read, so an empty source list
                  means no reading arrived, named by `offLabel`. */}
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

      {/* The distribution, not just its argmax -- `dominant_emotion` alone
          hides a week split 40/35/25 behind one word. Rendered only when
          there's something to render. */}
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
                  <Tooltip formatter={(v, n) => [`${v} samples`, n]} />
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
      {/* "Measured, unusable" is a third state: a null average beside a
          nonzero sample count means every reading failed the quality gate,
          not that the headband never ran. */}
      {heartShown && heartSamples > 0 && ratio(highlights.heart_rate_bpm) === null && (
        <p className="mt-1 text-xs text-gray-600 dark:text-gray-400">
          Heart-rate samples were recorded but none met the quality threshold, so no average is shown.
        </p>
      )}
      {/* The consent lookup itself failing, distinct from the per-table
          failures below -- without it, a database problem would render
          identically to a student who declined. */}
      {consentFailed && (
        <p className="mt-1 text-xs text-amber-600 dark:text-amber-400">
          Consent settings could not be read, so heart and facial data were left out of this report — that is not a record of what was permitted.
        </p>
      )}
      {/* Named per table rather than one blanket warning -- the reads fail
          independently, and "EEG did not load" is a different thing to act on
          than "session count did not load". */}
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
 * At-home practice strategies for a student, with the control that generates
 * them.
 *
 * `source` is rendered rather than hidden: the backend answers from a fixed
 * rule set unless an optional local model passed its safety checks, and
 * which happened is worth showing to whoever acts on the advice.
 *
 * `signalsRetrieved` covers a different failure: when the aggregate behind
 * the report fails to load, the endpoint still answers with generic advice,
 * but the subtitle would otherwise claim it's built from this week. `false`
 * retracts that claim; undefined means a pre-field payload from a working read.
 */
export function StrategyPanel({ strategies, source, signalsRetrieved, loading, error, onGenerate }) {
  const signalsMissing = signalsRetrieved === false
  return (
    <div className="rounded-2xl border border-gray-100 dark:border-gray-800 bg-white dark:bg-gray-900 p-5 shadow-sm">
      <div className="flex items-start justify-between gap-3 flex-wrap mb-4">
        <div>
          <h3 className="font-black text-gray-900 dark:text-white flex items-center gap-2">
            <Sparkles size={18} className="text-violet-500" /> At-Home Learning Strategies
          </h3>
          {/* Swapped rather than annotated, so the claim and its correction
              don't sit next to each other contradicting one another. */}
          <p className="text-xs text-gray-600 mt-1 dark:text-gray-400">
            {signalsMissing
              ? <>General practice suggestions — this week&apos;s signal data could not be read. Learning indicators only — not medical or behavioural advice.</>
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
          {/* Above the list, not beside `source` below -- it changes how the
              items should be read, so it shouldn't come after them. */}
          {signalsMissing && (
            <p className="rounded-xl border border-amber-200 dark:border-amber-900/50 bg-amber-50 dark:bg-amber-900/20 px-3 py-2 text-xs text-amber-800 dark:text-amber-200">
              This week&apos;s signal data couldn&apos;t be loaded, so these are general suggestions rather than ones based on your child&apos;s report. Try again shortly.
            </p>
          )}
          {/* Index key: replaced wholesale each generation, never reordered,
              and strategy text isn't guaranteed unique. */}
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
