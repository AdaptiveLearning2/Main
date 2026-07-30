<<<<<<< HEAD
import { Activity, Brain, Eye, Radio, Zap } from 'lucide-react'
import { LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid } from 'recharts'

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
=======
import { Activity, Brain, Eye, ShieldCheck, Sparkles, TrendingDown, TrendingUp, Zap } from 'lucide-react'

function hasValue(v) {
  return v !== null && v !== undefined && v !== ''
}

export function formatSignalValue(value) {
  if (!hasValue(value)) return '—'
  const n = Number(value)
  if (Number.isNaN(n)) return String(value)
  const pct = n <= 1 ? n * 100 : n
  return `${Math.round(pct)}%`
}

function MiniMetric({ icon, label, value, sub, tone = 'indigo' }) {
>>>>>>> 4fa1ce3 (Add parent reports and signal safety features)
  const tones = {
    indigo: 'bg-indigo-50 text-indigo-700 dark:bg-indigo-900/30 dark:text-indigo-300',
    emerald: 'bg-emerald-50 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-300',
    rose: 'bg-rose-50 text-rose-700 dark:bg-rose-900/30 dark:text-rose-300',
    amber: 'bg-amber-50 text-amber-700 dark:bg-amber-900/30 dark:text-amber-300',
<<<<<<< HEAD
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
=======
    violet: 'bg-violet-50 text-violet-700 dark:bg-violet-900/30 dark:text-violet-300',
    slate: 'bg-slate-50 text-slate-700 dark:bg-slate-800 dark:text-slate-300',
  }
  return (
    <div className="rounded-xl border border-gray-100 dark:border-gray-800 bg-white dark:bg-gray-900 p-3">
      <div className={`w-8 h-8 rounded-lg flex items-center justify-center mb-2 ${tones[tone] || tones.indigo}`}>
        {icon}
      </div>
      <p className="text-xl font-black text-gray-900 dark:text-white leading-none">{value}</p>
      <p className="text-[11px] font-bold uppercase tracking-wider text-gray-400 mt-1">{label}</p>
      {sub && <p className="text-[11px] text-gray-400 mt-1">{sub}</p>}
    </div>
  )
}

function Bar({ label, value, tone = 'indigo' }) {
  const n = Number(value)
  const pct = Number.isNaN(n) ? 0 : Math.max(0, Math.min(100, n <= 1 ? n * 100 : n))
  const colors = {
    indigo: 'bg-indigo-500', emerald: 'bg-emerald-500', rose: 'bg-rose-500', amber: 'bg-amber-500', violet: 'bg-violet-500'
  }
  return (
    <div>
      <div className="flex items-center justify-between mb-1">
        <span className="text-xs font-semibold text-gray-600 dark:text-gray-400">{label}</span>
        <span className="text-xs font-black text-gray-900 dark:text-white">{hasValue(value) ? `${Math.round(pct)}%` : '—'}</span>
      </div>
      <div className="h-2 rounded-full bg-gray-100 dark:bg-gray-800 overflow-hidden">
        <div className={`h-full rounded-full ${colors[tone] || colors.indigo}`} style={{ width: `${pct}%` }} />
>>>>>>> 4fa1ce3 (Add parent reports and signal safety features)
      </div>
    </div>
  )
}

<<<<<<< HEAD
export function LiveSignalSummary({ report, title = 'Live Signal Snapshot' }) {
  const latest = report?.latest || {}
  const cog = latest.cognitive || {}
  const face = latest.face || {}
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
        <MiniMetric label="Face Attention" value={pct(face.attention)} icon={Eye} tone="sky" />
      </div>
      <div className="mt-4 grid md:grid-cols-2 gap-3 text-sm">
        <div className="rounded-xl bg-slate-50 dark:bg-gray-800 p-3">
          <p className="text-xs font-bold uppercase tracking-widest text-gray-400">Facial Emotion</p>
          <p className="font-bold text-gray-900 dark:text-white capitalize">{face.emotion || 'No data'}</p>
        </div>
        <div className="rounded-xl bg-slate-50 dark:bg-gray-800 p-3">
          <p className="text-xs font-bold uppercase tracking-widest text-gray-400">Identity Confidence</p>
          <p className="font-bold text-gray-900 dark:text-white">{pct(face.identity_confidence)}</p>
        </div>
      </div>
    </div>
  )
}

export function WeeklySignalReport({ report, title = 'Weekly EEG & Face Report' }) {
  const avg = report?.averages || {}
  const highlights = report?.highlights || {}
  const counts = report?.sample_counts || {}
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
        <MiniMetric label="Face Attention" value={pct(avg.face_attention)} icon={Eye} tone="sky" />
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
              <Line type="monotone" dataKey="attention" stroke="#0ea5e9" strokeWidth={2} dot={false} name="Face Attention" />
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
          <p className="font-bold text-gray-900 dark:text-white capitalize">{highlights.dominant_emotion || 'N/A'}</p>
        </div>
      </div>

      <p className="mt-4 text-sm text-gray-500 dark:text-gray-400">{report?.summary || 'No summary available yet.'}</p>
      {report?.truncated && (
        <p className="mt-1 text-xs text-amber-600 dark:text-amber-400">
          Showing the most recent samples only — earlier days in this range exceeded the retrieval limit.
          {unretrieved > 0 && ` ${unretrieved} ${unretrieved === 1 ? 'day is' : 'days are'} shown as a gap because the data could not be retrieved, not because there was no activity.`}
        </p>
=======
export function FacialRecognitionToggle({ enabled, onChange }) {
  return (
    <div className="flex items-start justify-between gap-4 rounded-xl border border-gray-100 dark:border-gray-800 bg-slate-50 dark:bg-gray-950/40 p-4">
      <div className="flex gap-3">
        <div className="w-9 h-9 rounded-lg bg-violet-100 dark:bg-violet-900/40 text-violet-700 dark:text-violet-300 flex items-center justify-center">
          <Eye size={17} />
        </div>
        <div>
          <p className="text-sm font-black text-gray-900 dark:text-white">Include facial recognition data</p>
          <p className="text-xs text-gray-500 dark:text-gray-400 mt-0.5">Toggle facial-signal reporting on or off for this view. This does not start a camera by itself.</p>
        </div>
      </div>
      <button
        type="button"
        onClick={() => onChange?.(!enabled)}
        className={`relative w-12 h-7 rounded-full transition ${enabled ? 'bg-violet-600' : 'bg-gray-300 dark:bg-gray-700'}`}
        aria-pressed={enabled}
      >
        <span className={`absolute top-1 w-5 h-5 bg-white rounded-full shadow transition ${enabled ? 'left-6' : 'left-1'}`} />
      </button>
    </div>
  )
}

export function LiveSignalSummary({ cognitive, face, includeFace = true, title = 'Live Signal Snapshot' }) {
  const hasCog = !!cognitive
  const hasFace = includeFace && !!face
  return (
    <div className="bg-white dark:bg-gray-900 rounded-2xl border border-gray-100 dark:border-gray-800 p-5 shadow-sm">
      <div className="flex items-center justify-between gap-3 mb-4">
        <div>
          <h3 className="font-black text-gray-900 dark:text-white">{title}</h3>
          <p className="text-xs text-gray-500 dark:text-gray-400 mt-0.5">Latest available learning-state readings.</p>
        </div>
        <Activity size={18} className="text-indigo-500" />
      </div>
      {!hasCog && !hasFace ? (
        <p className="text-sm text-gray-400 py-4">No live EEG or facial recognition readings are available yet.</p>
      ) : (
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
          <MiniMetric icon={<Brain size={16} />} label="Focus" value={formatSignalValue(cognitive?.focus)} tone="emerald" />
          <MiniMetric icon={<Zap size={16} />} label="Stress" value={formatSignalValue(cognitive?.stress)} tone="rose" />
          <MiniMetric icon={<TrendingUp size={16} />} label="Engagement" value={formatSignalValue(cognitive?.engagement)} tone="indigo" />
          <MiniMetric icon={<Eye size={16} />} label="Face attention" value={includeFace ? formatSignalValue(face?.attention) : 'Off'} sub={includeFace ? face?.emotion : 'Facial reporting disabled'} tone="violet" />
        </div>
      )}
    </div>
  )
}

export function WeeklySignalReport({ report, includeFace = true, title = 'Weekly EEG & Face Report' }) {
  const eeg = report?.eeg || {}
  const face = includeFace ? (report?.face || {}) : null
  const performance = report?.performance || {}
  const topics = report?.topics || []
  const hasAny = report && (eeg.sample_count || face?.sample_count || performance.total_questions || topics.length)

  return (
    <div className="bg-white dark:bg-gray-900 rounded-2xl border border-gray-100 dark:border-gray-800 p-5 shadow-sm space-y-5">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h3 className="font-black text-gray-900 dark:text-white">{title}</h3>
          <p className="text-xs text-gray-500 dark:text-gray-400 mt-0.5">Last {report?.period_days || 7} days · learning-state indicators, not medical measurements.</p>
        </div>
        <ShieldCheck size={18} className="text-emerald-500" />
      </div>

      {!hasAny ? (
        <p className="text-sm text-gray-400 py-4">No weekly performance, EEG, or facial recognition data is available yet.</p>
      ) : (
        <>
          <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
            <MiniMetric icon={<TrendingUp size={16} />} label="Accuracy" value={hasValue(performance.accuracy) ? `${performance.accuracy}%` : '—'} sub={`${performance.total_questions || 0} questions`} tone="indigo" />
            <MiniMetric icon={<Brain size={16} />} label="Avg focus" value={formatSignalValue(eeg.avg_focus)} sub={eeg.sample_count ? `${eeg.sample_count} EEG readings` : 'no EEG data'} tone="emerald" />
            <MiniMetric icon={<Zap size={16} />} label="Avg stress" value={formatSignalValue(eeg.avg_stress)} sub={hasValue(eeg.highest_stress) ? `peak ${formatSignalValue(eeg.highest_stress)}` : 'no stress peak'} tone="rose" />
            <MiniMetric icon={<Eye size={16} />} label="Face attention" value={includeFace ? formatSignalValue(face?.avg_attention) : 'Off'} sub={includeFace ? (face?.dominant_emotion || 'no emotion data') : 'facial reporting disabled'} tone="violet" />
          </div>

          <div className="grid lg:grid-cols-2 gap-5">
            <div className="space-y-3">
              <p className="text-xs font-bold uppercase tracking-widest text-gray-400">Signal trends</p>
              <Bar label="Average focus" value={eeg.avg_focus} tone="emerald" />
              <Bar label="Average stress" value={eeg.avg_stress} tone="rose" />
              <Bar label="Engagement" value={eeg.avg_engagement} tone="indigo" />
              {includeFace && <Bar label="Facial attention" value={face?.avg_attention} tone="violet" />}
            </div>
            <div>
              <p className="text-xs font-bold uppercase tracking-widest text-gray-400 mb-3">Topic snapshot</p>
              {topics.length === 0 ? (
                <p className="text-sm text-gray-400">No topic breakdown is available yet.</p>
              ) : (
                <div className="space-y-3 max-h-60 overflow-y-auto pr-1">
                  {topics.slice(0, 8).map(t => (
                    <Bar key={t.topic_id || t.topic_name} label={(t.topic_name || 'Topic').replace('_', ' ')} value={t.accuracy} tone="amber" />
                  ))}
                </div>
              )}
            </div>
          </div>

          <div className="rounded-xl bg-amber-50 dark:bg-amber-900/20 border border-amber-100 dark:border-amber-900/40 p-3 text-xs text-amber-800 dark:text-amber-200">
            <strong>Safety note:</strong> EEG and facial values are classroom learning indicators only. They should not be used as medical, emotional, or behavioral diagnoses.
          </div>
        </>
      )}
    </div>
  )
}

export function StrategyPanel({ strategies, source }) {
  return (
    <div className="bg-white dark:bg-gray-900 rounded-2xl border border-gray-100 dark:border-gray-800 p-5 shadow-sm">
      <div className="flex items-center gap-2 mb-4">
        <Sparkles size={18} className="text-violet-500" />
        <h3 className="font-black text-gray-900 dark:text-white">At-Home Learning Strategies</h3>
      </div>
      {!strategies?.length ? (
        <p className="text-sm text-gray-400">Generate strategies after weekly data is loaded.</p>
      ) : (
        <div className="space-y-3">
          {strategies.map((s, i) => (
            <div key={i} className="flex gap-3 rounded-xl bg-violet-50 dark:bg-violet-900/20 border border-violet-100 dark:border-violet-900/40 p-3">
              <span className="w-6 h-6 rounded-lg bg-violet-600 text-white flex items-center justify-center text-xs font-black flex-shrink-0">{i + 1}</span>
              <p className="text-sm text-gray-700 dark:text-gray-200">{s}</p>
            </div>
          ))}
          {source && <p className="text-[11px] text-gray-400">Generated by: {source}</p>}
        </div>
>>>>>>> 4fa1ce3 (Add parent reports and signal safety features)
      )}
    </div>
  )
}
