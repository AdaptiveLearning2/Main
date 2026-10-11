import useAdminRead from '../../hooks/useAdminRead'
import { ReadState, Unread } from './adminUi'

const ADAPTIVE = '/api/admin/adaptive?days=14'
const POLL_MS = 5 * 60_000

// LLM_topic_decider.BIAS_WHYS, in words.
const WHYS = {
  // Fusion's `stressed`: EEG calm below its line, or heart rate raised (signal_fusion.py).
  stressed: 'Eased: low calm or a raised heart rate',
  manual: 'Set by the student (Easier / Harder)',
  facial_veto: 'Held: the camera withheld an increase',
  recent_misses: 'Held: recent misses',
  focused: 'Raised: the headband read focus',
  correct_run: 'Raised: a run of correct answers',
  nothing_to_act_on: 'Held: nothing to act on',
}

function Day({ d, withheld }) {
  if (d.withheld) {
    return (
      <tr className="border-t border-gray-100 dark:border-gray-800 text-gray-900 dark:text-white">
        <td className="py-1 align-top">{d.day}</td>
        <td colSpan={2} className="py-1 text-gray-600 dark:text-gray-400">Withheld: {withheld}</td>
      </tr>
    )
  }
  const whys = Object.entries(d.by_why || {}).sort((a, b) => b[1] - a[1])
  return (
    <tr className="border-t border-gray-100 dark:border-gray-800 text-gray-900 dark:text-white">
      <td className="py-1 align-top">{d.day}</td>
      <td className="py-1 align-top tabular-nums">
        {d.eased} eased, {d.raised} raised, {d.held} held
        <span className="block text-gray-600 dark:text-gray-400">{d.decisions} questions, {d.students} students</span>
      </td>
      <td className="py-1 align-top">
        <ul>
          {whys.map(([why, n]) => <li key={why}><span className="tabular-nums font-bold">{n}</span> × {WHYS[why] || why}</li>)}
        </ul>
      </td>
    </tr>
  )
}

// A write that keeps failing would otherwise read as a quiet fortnight.
function WriteFailures({ n, days }) {
  if (n == null) return <p className="text-xs text-gray-600 dark:text-gray-400">Failed decision writes could not be read.</p>
  if (n === 0) return null
  return (
    <p role="alert" className="text-xs text-amber-800 dark:text-amber-300">
      {n} decision{n === 1 ? '' : 's'} in the last {days} days could not be stored, so the figures here are short by that many.
    </p>
  )
}

function Decisions({ data }) {
  return (
    <div className="space-y-2">
      <DecisionDays data={data} />
      <WriteFailures n={data.write_failures} days={data.days} />
    </div>
  )
}

function DecisionDays({ data }) {
  if (!data.retrieved) return <Unread what="Adaptive decisions" />
  if (data.decisions.length === 0) {
    return <p className="text-sm text-gray-600 dark:text-gray-400">No adaptive decision recorded in the last {data.days} days.</p>
  }
  const withheld = `fewer than ${data.min_students} students`
  return (
    <div className="space-y-2">
      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead>
            <tr className="text-left text-gray-600 dark:text-gray-400">
              <th className="py-1">Day</th><th className="py-1">Difficulty</th><th className="py-1">Why</th>
            </tr>
          </thead>
          <tbody>{data.decisions.map(d => <Day key={d.day} d={d} withheld={withheld} />)}</tbody>
        </table>
      </div>
      <p className="text-xs text-gray-600 dark:text-gray-400">
        Each served adaptive question, by how its difficulty was set. A decision that drew on a heart or camera
        reading the student has since withdrawn is left out; a day with {withheld} is withheld.
      </p>
    </div>
  )
}

/** Engine's panel: adaptive decisions by school day. */
export default function AdaptiveDecisions() {
  const res = useAdminRead(ADAPTIVE, POLL_MS)
  return <ReadState res={res} what="adaptive decisions">{data => <Decisions data={data} />}</ReadState>
}
