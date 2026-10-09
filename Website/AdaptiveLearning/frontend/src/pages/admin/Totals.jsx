import useAdminRead from '../../hooks/useAdminRead'
import { ReadState, Unread } from './adminUi'

const FUNNEL = '/api/admin/funnel'
const CONSENT_OPS = '/api/admin/consent-ops?weeks=8'
const QUALITY = '/api/admin/signal-quality?days=14'
const POLL_MS = 5 * 60_000

const STEPS = {
  students: [['signed_up', 'Signed up'], ['joined_class', 'Joined a class'], ['parent_linked', 'Linked to a parent'],
             ['consenting_now', 'Some sensor consented now'], ['had_a_lesson', 'Had a lesson'],
             ['had_a_headband_lesson', 'Had a headband lesson']],
  teachers: [['signed_up', 'Signed up'], ['made_a_class', 'Made a class'],
             ['class_has_a_student', 'Has a student in a class']],
  parents: [['signed_up', 'Signed up'], ['linked_a_child', 'Linked to a child']],
}

const CHANNELS = { eeg: 'EEG', headband_optical: 'Headband heart', camera: 'Camera' }
const KINDS = { withdrawn: 'withdrawn', parent_enabled: 'turned on by a parent', erased: 'erased' }
const SOURCES = { muse_optics: 'Headband', rppg: 'Camera' }

function FunnelRole({ role, counts }) {
  const steps = STEPS[role]
  const first = counts[steps[0][0]] || 0
  return (
    <div className="bg-white dark:bg-gray-900 border border-gray-100 dark:border-gray-800 rounded-xl px-4 py-3">
      <p className="text-xs font-black uppercase tracking-wide text-gray-600 dark:text-gray-400 capitalize">{role}</p>
      <ol className="mt-2 space-y-1">
        {steps.map(([key, label]) => (
          <li key={key} className="flex items-baseline justify-between gap-3 text-sm text-gray-900 dark:text-white">
            <span>{label}</span>
            <span className="tabular-nums font-bold">
              {counts[key]}
              {key !== steps[0][0] && first > 0 && (
                <span className="ml-1 text-xs font-normal text-gray-600 dark:text-gray-400">
                  ({Math.round((100 * counts[key]) / first)}%)
                </span>
              )}
            </span>
          </li>
        ))}
      </ol>
    </div>
  )
}

/** Overview's card: how many of each role have reached each step. */
export function Funnel() {
  const res = useAdminRead(FUNNEL, POLL_MS)
  return (
    <ReadState res={res} what="the adoption funnel">
      {data => !data.retrieved ? <Unread what="The adoption funnel" /> : (
        <div className="space-y-2">
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
            {Object.keys(STEPS).map(role => <FunnelRole key={role} role={role} counts={data.funnel[role]} />)}
          </div>
          <p className="text-xs text-gray-600 dark:text-gray-400">
            Accounts as they stand now. Each step counts everyone who has done it, so a later step can be larger.
          </p>
        </div>
      )}
    </ReadState>
  )
}

function ConsentWeeks({ data }) {
  if (!data.retrieved) return <Unread what="Consent changes" />
  const thisWeek = data.changes.filter(c => c.week === data.changes.at(-1)?.week)
  const weeks = [...new Set(data.changes.map(c => c.week))]
  const total = (week, kind) => data.changes.filter(c => c.week === week && c.kind === kind)
    .reduce((n, c) => n + c.n, 0)
  return (
    <div className="space-y-3">
      {data.changes.length === 0
        ? <p className="text-sm text-gray-600 dark:text-gray-400">No consent change in the last {data.weeks} weeks.</p>
        : (
          <>
            <ul className="text-sm space-y-1">
              {thisWeek.map(c => (
                <li key={`${c.kind}-${c.channel}`} className="text-gray-900 dark:text-white">
                  <span className="tabular-nums font-bold">{c.n}</span> × {CHANNELS[c.channel] || c.channel}{' '}
                  {KINDS[c.kind] || c.kind}
                </li>
              ))}
            </ul>
            <p className="text-xs text-gray-600 dark:text-gray-400">Latest week with a change: week of {weeks.at(-1)}.</p>
            <table className="w-full text-xs">
              <thead>
                <tr className="text-left text-gray-600 dark:text-gray-400">
                  <th className="py-1">Week of</th><th className="py-1 text-right">Withdrawn</th>
                  <th className="py-1 text-right">Turned on</th><th className="py-1 text-right">Erased</th>
                </tr>
              </thead>
              <tbody>
                {weeks.map(w => (
                  <tr key={w} className="border-t border-gray-100 dark:border-gray-800 text-gray-900 dark:text-white">
                    <td className="py-1">{w}</td>
                    <td className="py-1 text-right tabular-nums">{total(w, 'withdrawn')}</td>
                    <td className="py-1 text-right tabular-nums">{total(w, 'parent_enabled')}</td>
                    <td className="py-1 text-right tabular-nums">{total(w, 'erased')}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </>
        )}
      <p className="text-xs text-gray-600 dark:text-gray-400">
        Parent turn-ons are recorded from {data.enablements_recorded_from}. Erasures count the latest per student and
        sensor, so they are a lower bound.
      </p>
    </div>
  )
}

/** Overview's card: consent withdrawals, parent turn-ons and erasures by week. */
export function ConsentChanges() {
  const res = useAdminRead(CONSENT_OPS, POLL_MS)
  return <ReadState res={res} what="consent changes">{data => <ConsentWeeks data={data} />}</ReadState>
}

const pct = (part, whole) => (whole ? `${Math.round((100 * part) / whole)}%` : '—')
const secs = s => (s == null ? '—' : `${Math.round(s)} s`)

function QualityDays({ data }) {
  if (!data.retrieved) return <Unread what="Signal quality" />
  const withheld = `fewer than ${data.min_students} students`
  if (data.eeg.length === 0 && data.heart.length === 0) {
    return <p className="text-sm text-gray-600 dark:text-gray-400">No signal recorded in the last {data.days} days.</p>
  }
  return (
    <div className="space-y-4">
      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <caption className="text-left text-xs font-bold text-gray-900 dark:text-white pb-1">EEG</caption>
          <thead>
            <tr className="text-left text-gray-600 dark:text-gray-400">
              <th className="py-1">Day</th><th className="py-1 text-right">Students</th>
              <th className="py-1 text-right">Usable readings</th>
            </tr>
          </thead>
          <tbody>
            {data.eeg.map(d => (
              <tr key={d.day} className="border-t border-gray-100 dark:border-gray-800 text-gray-900 dark:text-white">
                <td className="py-1">{d.day}</td>
                {d.withheld
                  ? <td colSpan={2} className="py-1 text-right text-gray-600 dark:text-gray-400">Withheld: {withheld}</td>
                  : <>
                      <td className="py-1 text-right tabular-nums">{d.students}</td>
                      <td className="py-1 text-right tabular-nums">
                        {pct(d.trusted, d.samples)} of {d.samples}
                        {d.partial && (
                          <span className="block text-gray-600 dark:text-gray-400">so far: ended lessons only</span>
                        )}
                      </td>
                    </>}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <caption className="text-left text-xs font-bold text-gray-900 dark:text-white pb-1">Heart</caption>
          <thead>
            <tr className="text-left text-gray-600 dark:text-gray-400">
              <th className="py-1">Day</th><th className="py-1">Sensor</th><th className="py-1 text-right">Students</th>
              <th className="py-1 text-right">Signal quality 0.8+</th><th className="py-1 text-right">Calibrated in (median / 90%)</th>
              <th className="py-1 text-right">Never calibrated</th><th className="py-1 text-right">Simulated</th>
            </tr>
          </thead>
          <tbody>
            {data.heart.map(d => {
              const deciles = d.sqi_deciles || {}
              const all = Object.values(deciles).reduce((a, b) => a + b, 0)
              const good = (deciles['9'] || 0) + (deciles['10'] || 0)
              return (
                <tr key={`${d.day}-${d.source}`}
                    className="border-t border-gray-100 dark:border-gray-800 text-gray-900 dark:text-white">
                  <td className="py-1">{d.day}</td>
                  <td className="py-1">{SOURCES[d.source] || d.source}</td>
                  {d.withheld
                    ? <td colSpan={5} className="py-1 text-right text-gray-600 dark:text-gray-400">Withheld: {withheld}</td>
                    : <>
                        <td className="py-1 text-right tabular-nums">{d.students}</td>
                        <td className="py-1 text-right tabular-nums">{pct(good, all)}</td>
                        <td className="py-1 text-right tabular-nums">
                          {secs(d.median_seconds_to_calibrate)} / {secs(d.p90_seconds_to_calibrate)}
                        </td>
                        <td className="py-1 text-right tabular-nums">
                          {d.never_calibrated} of {d.sessions}
                          {d.still_calibrating > 0 && (
                            <span className="block text-gray-600 dark:text-gray-400">
                              {d.still_calibrating} still calibrating
                            </span>
                          )}
                        </td>
                        <td className="py-1 text-right tabular-nums">{pct(d.synthetic_rows, d.rows)}</td>
                      </>}
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
      <p className="text-xs text-gray-600 dark:text-gray-400">
        School days in {data.timezone}. Heart counts only students whose consent for that sensor is on now; EEG is
        always counted. A day with {withheld} is withheld so no one can be singled out.
      </p>
    </div>
  )
}

/** Engine's panel: signal quality by school day, aggregates only. */
export function SignalQuality() {
  const res = useAdminRead(QUALITY, POLL_MS)
  return <ReadState res={res} what="signal quality">{data => <QualityDays data={data} />}</ReadState>
}
