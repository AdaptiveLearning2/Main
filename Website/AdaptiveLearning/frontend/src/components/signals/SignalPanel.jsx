import { Activity, Brain, Eye, Radio, Sparkles, Zap } from 'lucide-react'
import { LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid } from 'recharts'

// Whether the report in hand actually contains facial data. The backend sets
// face_included=false when the viewer opted out, which nulls every face field
// -- indistinguishable from "the camera recorded nothing" without this flag.
// Older payloads predate the field, so absent means included.
function faceIncluded(report) {
  return report?.face_included !== false
}

const FACE_OFF = 'Off'

// Signal values cross the wire as 0..1 ratios -- that is what cognitive_signals
// and face_signals store, and what /live and the parent dashboard tiles both
// assume (Live.jsx's Gauge and Dashboard.jsx both scale by 100 at render).
// Rendering them without scaling printed focus 0.72 as "1%", i.e. every metric
// on this panel came out ~100x too small.
function pct(ratio) {
  if (ratio === null || ratio === undefined || Number.isNaN(Number(ratio))) return 'N/A'
  return `${Math.round(Number(ratio) * 100)}%`
}

// Same scaling for chart series. Nulls must stay null rather than becoming 0:
// a day with no retrievable data should leave a gap, not draw a line at the
// floor claiming zero focus.
function toPct(ratio) {
  return ratio === null || ratio === undefined || Number.isNaN(Number(ratio))
    ? null
    : Number(ratio) * 100
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
  const faceOn = faceIncluded(report)
  return (
    <div className="rounded-2xl border border-gray-100 dark:border-gray-800 bg-white dark:bg-gray-900 p-5 shadow-sm">
      <div className="mb-4 flex items-center justify-between gap-3">
        <div>
          <h3 className="font-black text-gray-900 dark:text-white">{title}</h3>
          <p className="text-xs text-gray-400">Most recent EEG and facial-recognition readings.</p>
        </div>
        <Radio size={18} className="text-emerald-500" />
      </div>
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
        <MiniMetric label="Focus" value={pct(cog.focus)} icon={Brain} tone="emerald" />
        <MiniMetric label="Stress" value={pct(cog.stress)} icon={Zap} tone="rose" />
        <MiniMetric label="Engagement" value={pct(cog.engagement)} icon={Activity} tone="indigo" />
        {/* "Off" rather than "N/A": the viewer switched facial reporting off,
            which is a different statement from having no reading. */}
        <MiniMetric label="Face Attention" value={faceOn ? pct(face.attention) : FACE_OFF} icon={Eye} tone="sky" />
      </div>
      <div className="mt-4 grid md:grid-cols-2 gap-3 text-sm">
        <div className="rounded-xl bg-slate-50 dark:bg-gray-800 p-3">
          <p className="text-xs font-bold uppercase tracking-widest text-gray-400">Facial Emotion</p>
          <p className="font-bold text-gray-900 dark:text-white capitalize">
            {faceOn ? (face.emotion || 'No data') : 'Reporting off'}
          </p>
        </div>
        <div className="rounded-xl bg-slate-50 dark:bg-gray-800 p-3">
          <p className="text-xs font-bold uppercase tracking-widest text-gray-400">Identity Confidence</p>
          <p className="font-bold text-gray-900 dark:text-white">
            {faceOn ? pct(face.identity_confidence) : 'Reporting off'}
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
  const faceOn = faceIncluded(report)
  // Scaled to percent to match the YAxis domain below. Left as 0..1 ratios,
  // every series drew flat along the axis floor.
  const chartData = (report?.daily || []).map(d => ({
    ...d,
    focus: toPct(d.focus),
    stress: toPct(d.stress),
    attention: toPct(d.attention),
    label: d.date ? d.date.slice(5) : '',
  }))
  // Days the row cap kept us from retrieving, as opposed to days with no
  // activity. Both render as a gap, so the difference has to be stated.
  const unretrieved = (report?.daily || []).filter(
    d => d.cognitive_retrieved === false || d.face_retrieved === false
  ).length

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
        <MiniMetric label="Face Attention" value={faceOn ? pct(avg.face_attention) : FACE_OFF} icon={Eye} tone="sky" />
        <MiniMetric label="Sessions" value={counts.sessions ?? 0} icon={Radio} tone="amber" />
      </div>

      <div className="h-56 rounded-2xl bg-slate-50 dark:bg-gray-800 p-3">
        {chartData.length === 0 ? (
          <div className="h-full flex items-center justify-center text-sm text-gray-400">No weekly signal data available yet.</div>
        ) : (
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={chartData} margin={{ top: 10, right: 10, left: -20, bottom: 0 }}>
              <CartesianGrid strokeDasharray="3 3" opacity={0.25} />
              <XAxis dataKey="label" fontSize={11} tickLine={false} />
              <YAxis domain={[0, 100]} fontSize={11} tickLine={false} />
              <Tooltip />
              {/* Distinct colours per series -- all three were "currentColor",
                  which rendered them identically and made the chart unreadable.
                  Matches the MiniMetric tones above. */}
              <Line type="monotone" dataKey="focus" stroke="#10b981" strokeWidth={2} dot={false} name="Focus" />
              <Line type="monotone" dataKey="stress" stroke="#f43f5e" strokeWidth={2} dot={false} name="Stress" />
              {/* Omitted entirely with facial reporting off, rather than drawn
                  as an all-null series -- an empty legend entry reads as a
                  measurement that flatlined. */}
              {faceOn && <Line type="monotone" dataKey="attention" stroke="#0ea5e9" strokeWidth={2} dot={false} name="Face Attention" />}
            </LineChart>
          </ResponsiveContainer>
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
          <p className="font-bold text-gray-900 dark:text-white capitalize">
            {faceOn ? (highlights.dominant_emotion || 'N/A') : 'Reporting off'}
          </p>
        </div>
      </div>

      <p className="mt-4 text-sm text-gray-500 dark:text-gray-400">{report?.summary || 'No summary available yet.'}</p>
      {report && !faceOn && (
        <p className="mt-1 text-xs text-gray-400">
          Facial recognition data was not included in this report.
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
 * Switch controlling whether facial-recognition data is read into a report.
 *
 * The copy is deliberate: this decides what the report *contains*, and the
 * backend skips the face_signals query outright when it is off. It does not
 * control the camera, and saying so avoids implying a hardware guarantee this
 * switch cannot make.
 */
export function FacialRecognitionToggle({ enabled, onChange, disabled = false }) {
  return (
    <div className="flex items-start justify-between gap-4 rounded-2xl border border-gray-100 dark:border-gray-800 bg-white dark:bg-gray-900 p-4 shadow-sm">
      <div className="flex gap-3">
        <div className="w-9 h-9 rounded-xl bg-sky-50 text-sky-700 dark:bg-sky-900/30 dark:text-sky-300 flex items-center justify-center shrink-0">
          <Eye size={17} />
        </div>
        <div>
          <p className="text-sm font-black text-gray-900 dark:text-white">Include facial recognition data</p>
          <p className="text-xs text-gray-500 dark:text-gray-400 mt-0.5">
            When off, facial signals are left out of this report and are not read. This does not switch a camera on or off.
          </p>
        </div>
      </div>
      <button
        type="button"
        role="switch"
        aria-checked={enabled}
        aria-label="Include facial recognition data"
        disabled={disabled}
        onClick={() => onChange?.(!enabled)}
        className={`relative w-12 h-7 rounded-full shrink-0 transition disabled:opacity-50 ${enabled ? 'bg-sky-600' : 'bg-gray-300 dark:bg-gray-700'}`}
      >
        <span className={`absolute top-1 w-5 h-5 bg-white rounded-full shadow transition-all ${enabled ? 'left-6' : 'left-1'}`} />
      </button>
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
 */
export function StrategyPanel({ strategies, source, loading, error, onGenerate }) {
  return (
    <div className="rounded-2xl border border-gray-100 dark:border-gray-800 bg-white dark:bg-gray-900 p-5 shadow-sm">
      <div className="flex items-start justify-between gap-3 flex-wrap mb-4">
        <div>
          <h3 className="font-black text-gray-900 dark:text-white flex items-center gap-2">
            <Sparkles size={18} className="text-violet-500" /> At-Home Learning Strategies
          </h3>
          <p className="text-xs text-gray-400 mt-1">
            Practice suggestions built from this week&apos;s report. Learning indicators only — not medical or behavioural advice.
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
