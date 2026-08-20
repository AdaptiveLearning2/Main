import { Activity, Brain, Heart, Radio, Sparkles, Zap } from 'lucide-react'
import {
  LineChart, Line, XAxis, YAxis, Tooltip, CartesianGrid,
  PieChart, Pie, Cell, Legend,
} from 'recharts'
import { describeSlices } from '../charts/describeSeries'
import AccessibleChart from '../charts/AccessibleChart'

// One string cannot answer for three channels any more, so this is a function
// of *which* channel and *why* it is not showing a value. The old `FACE_OFF =
// 'Off'` meant "the viewer switched facial reporting off", which was true when
// there was one viewer-side switch and is now wrong in three different ways:
// the channel may be off because the student withdrew it, because the sensor
// is not present, or because the read failed.
//
// The distinction the reporting rules exist for: **never render "no data" for
// something that was not recorded.** A tile saying "N/A" for a withdrawn
// channel reports an absence in data that was never collected, and one saying
// "Off" for a failed read reports a preference that nobody expressed.
const CHANNEL_STATE = {
  // Consent: the channel is off, and we know when. `since` is the date the
  // student or a parent switched it off.
  revoked: since => (since ? `Off since ${since}` : 'Not recorded'),
  // The consent read itself failed. Distinct from revoked, because saying "the
  // student turned this off" when we could not find out is a claim we have not
  // earned.
  unknown: () => 'Unavailable',
  // Consented and present, but this window produced nothing usable -- a
  // rejected reading, or a baseline still being established.
  calibrating: () => 'Calibrating',
  // Consented, but no sensor produced anything at all this period.
  noSensor: () => 'No sensor',
}

// Formats an ISO timestamp as a short date, or null when there isn't one --
// so a caller can tell "off, and here is when" from "off, and we don't know
// when", rather than printing "Invalid Date" at a parent.
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
  // On, and read, but empty. `samples > 0` means readings arrived and none was
  // usable -- which is the calibrating/rejected case, not an absent sensor.
  return samples > 0 ? CHANNEL_STATE.calibrating() : CHANNEL_STATE.noSensor()
}

/**
 * A rendered value, or the reason there isn't one.
 *
 * Every tile goes through here rather than branching on the channel flag
 * directly. Branching only on "is the channel on" left `pct()`'s own 'N/A'
 * standing whenever a consented channel produced nothing usable -- which is
 * the string this whole change exists to stop showing, surviving in the case
 * the change was least likely to be tested against.
 */
export function valueOrReason(value, reason) {
  return (value && value !== 'N/A') ? value : offLabel(reason)
}

// One colour per FER+ label, fixed rather than positional: a palette assigned by
// slice order would recolour every emotion whenever the distribution changed,
// so the same week viewed twice would look like different data.
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

// Signal values cross the wire as 0..1 ratios -- that is what cognitive_signals
// and face_signals store, and what /live and the parent dashboard tiles both
// assume (Live.jsx's Gauge and Dashboard.jsx both scale by 100 at render).
// Rendering them without scaling printed focus 0.72 as "1%", i.e. every metric
// on this panel came out ~100x too small.
//
// A measurement, or null for anything that is not one. Guarding with
// Number.isNaN(Number(v)) was not enough, in two directions: Number('') and
// Number('  ') are both 0, so an empty value rendered as a confident "0%"
// rather than "N/A", and Number.isNaN(Infinity) is false, so a non-finite
// number passed straight through to Math.round. Converting first and asking
// Number.isFinite covers both -- the same reasoning as the null-dropping in
// Students.jsx, where Number(null) === 0 caused the mirror-image bug.
function ratio(value) {
  if (value === null || value === undefined) return null
  if (typeof value === 'string' && value.trim() === '') return null
  const n = Number(value)
  return Number.isFinite(n) ? n : null
}

// Exported: the parent dashboard renders the same 0..1 ratios in its own tiles
// and had a verbatim copy of this, which is how it kept the weakness above
// after this one was fixed. One definition, so the surfaces cannot disagree
// about what an unrenderable value looks like.
export function pct(value) {
  const n = ratio(value)
  return n === null ? 'N/A' : `${Math.round(n * 100)}%`
}

// Same scaling for chart series. Nulls must stay null rather than becoming 0:
// a day with no retrievable data should leave a gap, not draw a line at the
// floor claiming zero focus.
function toPct(value) {
  const n = ratio(value)
  return n === null ? null : n * 100
}

// Absolute units, not ratios. `toPct` must never touch these: a 72 bpm day put
// through it draws at 7200%, which is why the heart series gets its own axis
// rather than sharing the 0-100 one every other series uses.
function unit(value, suffix, digits = 0) {
  const n = ratio(value)
  return n === null ? 'N/A' : `${n.toFixed(digits)}${suffix}`
}

// Two channels are now consented separately, so one flag cannot answer for
// both. `face_included` is kept by the backend as a deprecated alias for the
// emotion channel; reading the new field first and falling back means a payload
// from before the split still renders correctly rather than reporting the
// channel as excluded because the field is absent.
export function emotionOn(report) {
  if (report?.emotion_included !== undefined) return report.emotion_included !== false
  // The legacy alias, inlined. It used to come from `lib/facePref.js`, which
  // is gone: that module was a viewer-side read filter and stored consent
  // replaced it. The flag itself is still emitted by the backend, so the
  // fallback stays -- absent means a payload from before the split, and
  // treating that as "excluded" would blank a channel that was recorded.
  return report?.face_included !== false
}

function heartOn(report) {
  // Absent means a pre-split payload, which had no heart data at all -- so the
  // honest default is off, not on. Defaulting to true would draw an empty
  // series and claim the sensor recorded nothing.
  return report?.heart_included === true
}

// The offLabel `reason` for every face-derived tile on this panel -- one
// definition, so a future change to which field backs the revocation date or
// sample count cannot update four call sites and miss the fifth, the way
// `pct()`'s own duplication (see its export comment) once let a fix to one
// copy leave the other silently wrong.
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
 * Missing until now, which is why the three cognitive tiles were the only ones
 * still rendering `pct()`'s raw 'N/A'. A headband recording with poor electrode
 * contact writes rows with the measurement columns nulled -- the honest thing,
 * so the session's timeline survives -- and the snapshot then read the newest
 * of those and printed "N/A" beside a weekly average of 64%. Two true numbers
 * that look like a contradiction, and the tile that should have said
 * "Calibrating" was the one saying nothing was there.
 *
 * `on` used to be hardcoded true, because the payload carried
 * `emotion_revoked_at` and `heart_revoked_at` and nothing at all for EEG --
 * so these three tiles were the only ones that could not say "Off since
 * <date>", and a parent who switched the headband off read "No sensor", which
 * is what a fault looks like rather than what they did. The payload now carries
 * `eeg_enabled` and `eeg_revoked_at`.
 *
 * `eeg_enabled` rather than an `eeg_included` matching the other two, and the
 * difference is real: the cognitive channel has no opt-out on the aggregate and
 * is always read, and a withdrawal deliberately keeps what was already
 * recorded. So a withdrawn EEG channel can still hold true averages from before
 * the withdrawal, which is exactly the case the date is for.
 *
 * Absent means a payload from before the field existed, and that has to read as
 * *on*: defaulting to off would tell every reader of an older payload that a
 * headband had been switched off, which is a claim about a decision nobody
 * made. Same fallback as `emotionOn`, opposite to `heartOn` -- where absent
 * genuinely meant the channel did not exist yet.
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
          <p className="text-xs font-bold uppercase tracking-widest text-gray-400">{label}</p>
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
  // `heart` is absent from `latest` when the channel was not read, so presence
  // is the signal -- the backend omits the key rather than sending a null, and
  // a tile rendered from `{}` would read as a sensor recording nothing.
  const heart = latest.heart
  const heartShown = heartOn(report) && !!heart
  return (
    <div className="rounded-2xl border border-gray-100 dark:border-gray-800 bg-white dark:bg-gray-900 p-5 shadow-sm">
      <div className="mb-4 flex items-center justify-between gap-3">
        <div>
          <h3 className="font-black text-gray-900 dark:text-white">{title}</h3>
          <p className="text-xs text-gray-400">
            Most recent EEG{heartShown ? ', heart' : ''} and facial-recognition readings.
          </p>
        </div>
        <Radio size={18} className="text-emerald-500" />
      </div>
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
        {/* Through `valueOrReason`, like every other tile. These three were the
            last holdouts still printing `pct()`'s raw 'N/A' -- so a headband
            recording with poor electrode contact, which writes rows with the
            measurement columns nulled, showed "N/A" here beside a weekly
            average of 64% on the same screen. */}
        <MiniMetric label="Focus" value={valueOrReason(pct(cog.focus), eegReason(report))}
                    icon={Brain} tone="emerald" />
        <MiniMetric label="Stress" value={valueOrReason(pct(cog.stress), eegReason(report))}
                    icon={Zap} tone="rose" />
        <MiniMetric label="Engagement" value={valueOrReason(pct(cog.engagement), eegReason(report))}
                    icon={Activity} tone="indigo" />
        {/* No attention tile. `face_signals.attention` has no producer -- nothing
            computes it and the column is always null -- so the tile could only
            ever say "Calibrating", which reads as a measurement warming up
            rather than one that will never arrive. Phase 11 step 3 is blocked
            on a labelled reference, not on code: attention inferred from head
            direction is least valid for exactly this product's users, and it
            renders as a percentage, which reads as objective. The column, the
            payload field and `face_geometry` all stay; only the claim goes. */}
        {/* bpm, not a percentage -- the same unit trap the weekly chart gives
            its own axis for. `unit()` never touches the 0..1 path. */}
        {heartShown && (
          <MiniMetric label="Heart Rate" value={unit(heart.heart_rate_bpm, ' bpm')} icon={Heart} tone="rose" />
        )}
      </div>
      {/* One tile, not two. "Identity Confidence" sat here and was retired in
          #86: face identity never had a producer, so the tile could only ever
          resolve to "No sensor", and identifying a child by face is a different
          purpose from the one the camera consent asks about. */}
      <div className="mt-4 grid gap-3 text-sm">
        <div className="rounded-xl bg-slate-50 dark:bg-gray-800 p-3">
          <p className="text-xs font-bold uppercase tracking-widest text-gray-400">Facial Emotion</p>
          {/* Not a binary faceOn ? value : "Reporting off": a failed consent
              read also leaves faceOn false, and offLabel is what tells that
              apart from a genuine withdrawal -- the same reason the Face
              Attention tile above goes through it rather than branching here. */}
          <p className="font-bold text-gray-900 dark:text-white capitalize">
            {valueOrReason(faceOn && face.emotion, faceReason(report, faceOn))}
          </p>
        </div>
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
  // Scaled to percent to match the YAxis domain below. Left as 0..1 ratios,
  // every series drew flat along the axis floor.
  const chartData = (report?.daily || []).map(d => ({
    ...d,
    focus: toPct(d.focus),
    stress: toPct(d.stress),
    // Deliberately NOT through toPct. These arrive in beats per minute and
    // milliseconds; scaling them by 100 would put them three orders of
    // magnitude off, and on a shared axis they would flatten every other series
    // against the floor. `ratio()` only sanitises -- null stays null so a day
    // with no reading leaves a gap rather than drawing at zero.
    heart_rate_bpm: ratio(d.heart_rate_bpm),
    label: d.date ? d.date.slice(5) : '',
  }))
  // Days the row cap kept us from retrieving, as opposed to days with no
  // activity. Both render as a gap, so the difference has to be stated.
  const unretrieved = (report?.daily || []).filter(
    d => d.cognitive_retrieved === false
      || d.face_retrieved === false
      || d.heart_retrieved === false
      || d.sessions_retrieved === false
  ).length
  // Which of the report's three reads happened. A failed query returns the same
  // empty rows as a quiet week, so every metric below it is a default rendered
  // as a measurement unless this says otherwise. `=== false` throughout:
  // undefined is a payload predating the field, which came from working reads
  // by definition, and null is the facial opt-out rather than a failure.
  const retrieved = report?.retrieved || {}
  const cogFailed = retrieved.cognitive === false
  const faceFailed = retrieved.face === false
  const sessionsFailed = retrieved.sessions === false
  const heartFailed = retrieved.heart === false
  const anyFailed = cogFailed || faceFailed || heartFailed || sessionsFailed
  // A separate failure from any of the above: the consent lookup that decides
  // which channels may be read at all. False means the channel flags mean "we
  // could not find out", not "the student declined" -- and every tile below
  // would otherwise read as a respected refusal.
  const consentFailed = report?.consent_retrieved === false
  // Counts, so "measured but unusable" is distinguishable from "never measured".
  // A week of nothing but untrusted samples is a null average beside a nonzero
  // count; without the count it looks like the sensor was off.
  const heartSamples = counts.heart || 0
  // Sorted by count so the legend reads in the order the slices appear, and
  // built from the tally the backend already computed to pick the dominant
  // emotion rather than recounting raw rows the frontend does not have.
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
        <p className="text-xs text-gray-400">Averages are based on the last {report?.days || 7} days of available samples.</p>
      </div>

      <div className="grid grid-cols-2 lg:grid-cols-5 gap-3 mb-5">
        <MiniMetric label="Avg Focus" value={pct(avg.focus)} icon={Brain} tone="emerald" />
        <MiniMetric label="Avg Stress" value={pct(avg.stress)} icon={Zap} tone="rose" />
        <MiniMetric label="Engagement" value={pct(avg.engagement)} icon={Activity} tone="indigo" />
        {/* sessions_recorded, not sample_counts.sessions: the latter is rows
            retrieved under the session row cap, so a heavy week showed exactly
            the cap here while the parent dashboard -- which counts in Postgres
            -- showed the real number for the same child and week. Falls back
            for payloads predating the field.

            A dash when the sessions read failed, not the 0 the fallback chain
            would otherwise reach: sessions_recorded is null on that path and
            sample_counts.sessions is the length of an empty list, so the
            fallback would answer a broken query with a confident "0 sessions
            this week". */}
        <MiniMetric
          label="Sessions"
          value={sessionsFailed ? '—' : (report?.sessions_recorded ?? counts.sessions ?? 0)}
          icon={Radio}
          tone="amber"
        />
      </div>

      <div className="h-56 rounded-2xl bg-slate-50 dark:bg-gray-800 p-3">
        {chartData.length === 0 ? (
          <div className="h-full flex items-center justify-center text-sm text-gray-400 text-center px-4">
            {/* A failed read empties the series exactly as a quiet week does,
                and every day is dropped when nothing could be retrieved for it
                -- so the "yet" claim has to be gated on having looked. */}
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
          <p className="text-xs font-bold uppercase tracking-widest text-gray-400">Highest Stress</p>
          <p className="font-bold text-gray-900 dark:text-white">{pct(highlights.highest_stress)}</p>
        </div>
        <div className="rounded-xl bg-slate-50 dark:bg-gray-800 p-3">
          <p className="text-xs font-bold uppercase tracking-widest text-gray-400">Lowest Focus</p>
          <p className="font-bold text-gray-900 dark:text-white">{pct(highlights.lowest_focus)}</p>
        </div>
        <div className="rounded-xl bg-slate-50 dark:bg-gray-800 p-3">
          <p className="text-xs font-bold uppercase tracking-widest text-gray-400">Dominant Emotion</p>
          {/* Not a binary faceOn ? value : "Reporting off" -- a failed consent
              read also leaves faceOn false, and offLabel is what tells that
              apart from an actual withdrawal. */}
          <p className="font-bold text-gray-900 dark:text-white capitalize">
            {valueOrReason(faceOn && highlights.dominant_emotion, faceReason(report, faceOn))}
          </p>
        </div>
      </div>

      {/* Three states, and the middle one is the point.
          - Read: values.
          - Explicitly not read (`heart_included === false`): the row stays, with
            *why* in place of the numbers. Dropping it here would tell a parent
            who switched the sensor off nothing at all, and the plan's rule is
            that a channel that is off says so and never reads as "no data".
          - Field absent: a payload predating the split, which had no heart data
            to describe. The row is omitted, because there is nothing true to
            say about a channel this payload does not know about. */}
      {report?.heart_included === false && (
        <div className="mt-3 rounded-xl bg-slate-50 dark:bg-gray-800 p-3 text-sm">
          <p className="text-xs font-bold uppercase tracking-widest text-gray-400">Heart</p>
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
            <p className="text-xs font-bold uppercase tracking-widest text-gray-400">Avg Heart Rate</p>
            {/* Not a raw "N/A": the channel was read (heartShown, above), so an
                empty average means the week's samples were unusable rather than
                the sensor never having been asked -- the same distinction the
                "Sensor" tile below draws for its own field. */}
            <p className="font-bold text-gray-900 dark:text-white">
              {valueOrReason(unit(highlights.heart_rate_bpm, ' bpm'), {
                on: true,
                consentRetrieved: report?.consent_retrieved,
                samples: report?.sample_counts?.heart,
              })}
            </p>
          </div>
          <div className="rounded-xl bg-slate-50 dark:bg-gray-800 p-3">
            <p className="text-xs font-bold uppercase tracking-widest text-gray-400">Avg RMSSD</p>
            <p className="font-bold text-gray-900 dark:text-white">
              {valueOrReason(unit(highlights.rmssd_ms, ' ms'), {
                on: true,
                consentRetrieved: report?.consent_retrieved,
                samples: report?.sample_counts?.heart,
              })}
            </p>
          </div>
          <div className="rounded-xl bg-slate-50 dark:bg-gray-800 p-3">
            <p className="text-xs font-bold uppercase tracking-widest text-gray-400">Sensor</p>
            {/* Named because accuracy differs materially by source, and the
                camera one is unvalidated -- a reader comparing two weeks should
                be able to see the sensor changed. */}
            <p className="font-bold text-gray-900 dark:text-white">
              {/* Not "N/A": the channel was read, so an empty source list
                  means no reading arrived, which `offLabel` names. */}
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

      {/* The distribution, not just its argmax. `dominant_emotion` alone hides
          a week split 40/35/25 behind one word. Rendered only when there is
          something to render: an empty pie is a claim about a quiet week that
          an unread channel has not earned. */}
      {faceOn && emotionSlices.length > 0 && (
        <div className="mt-4">
          <p className="text-xs font-bold uppercase tracking-widest text-gray-400 mb-1">Emotion Mix</p>
          <AccessibleChart className="h-52"
            summary={describeSlices('Emotion mix', emotionSlices, 'samples')}
            rows={emotionSlices} rowKey="name" rowLabel="Emotion"
            columns={[{ key: 'value', label: 'Samples' }]}>
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
        <p className="mt-1 text-xs text-gray-400">
          Facial recognition data was not included in this report.
        </p>
      )}
      {/* "Measured, unusable" is a third state. A null average beside a nonzero
          sample count means the headband was on and every reading failed its
          quality gate -- which is not the same as a week it never ran. */}
      {heartShown && heartSamples > 0 && ratio(highlights.heart_rate_bpm) === null && (
        <p className="mt-1 text-xs text-gray-400">
          Heart-rate samples were recorded but none met the quality threshold, so no average is shown.
        </p>
      )}
      {/* Distinct from the per-table failures below: this is the consent lookup
          itself failing, which suppresses every optional channel. Without it,
          a database problem renders identically to a student who declined. */}
      {consentFailed && (
        <p className="mt-1 text-xs text-amber-600 dark:text-amber-400">
          Consent settings could not be read, so heart and facial data were left out of this report — that is not a record of what was permitted.
        </p>
      )}
      {/* Named per table rather than as one blanket warning: the reads fail
          independently, and "the EEG figures did not load" is a different thing
          for a reader to act on than "the session count did not load". The
          tiles above show a dash or an N/A either way, which on its own reads
          as "nothing recorded". */}
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
 * rule set unless an optional local model produced output that passed its
 * safety checks, and which of those happened is worth showing to whoever is
 * about to act on the advice.
 *
 * `signalsRetrieved` is the same argument about a different failure. When the
 * aggregate behind the report does not load, the endpoint still answers -- the
 * rules read a null average as no signal to act on and fall through to their
 * generic branches, which is the right outcome. But the result is advice that
 * is not about this student's week, sitting under a subtitle that says it is.
 * False here retracts that claim. Undefined means a payload predating the
 * field, which came from a working read by definition.
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
          {/* The subtitle claims these were built from the report, which is
              the thing that is not true when the signals did not load. Swapped
              rather than annotated, so the two do not sit next to each other
              contradicting one another -- the same treatment the weekly
              report's summary sentence gets when facial data is excluded. */}
          <p className="text-xs text-gray-400 mt-1">
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
        <p className="text-sm text-gray-400">No strategies generated yet.</p>
      ) : (
        <div className="space-y-3">
          {/* Above the list rather than beside `source` below it: it changes
              how every item that follows should be read, and someone acting on
              the advice should not have to reach the end to find that out. */}
          {signalsMissing && (
            <p className="rounded-xl border border-amber-200 dark:border-amber-900/50 bg-amber-50 dark:bg-amber-900/20 px-3 py-2 text-xs text-amber-800 dark:text-amber-200">
              This week&apos;s signal data couldn&apos;t be loaded, so these are general suggestions rather than ones based on your child&apos;s report. Try again shortly.
            </p>
          )}
          {/* Index key: the list is replaced wholesale on each generation and
              never reordered, and strategy text is not guaranteed unique. */}
          {strategies.map((s, i) => (
            <div key={i} className="flex gap-3 rounded-xl bg-slate-50 dark:bg-gray-800 p-3">
              <span className="w-6 h-6 rounded-lg bg-violet-600 text-white flex items-center justify-center text-xs font-black shrink-0">{i + 1}</span>
              <p className="text-sm text-gray-700 dark:text-gray-200">{s}</p>
            </div>
          ))}
          {source && <p className="text-[11px] text-gray-400">Source: {source}</p>}
        </div>
      )}
    </div>
  )
}
