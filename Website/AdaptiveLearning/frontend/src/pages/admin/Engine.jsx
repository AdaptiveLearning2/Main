import { Link } from 'react-router-dom'
import useAdminRead from '../../hooks/useAdminRead'
import { ReadState, Tile, Unread } from './adminUi'
import { SignalQuality } from './Totals'
import AdaptiveDecisions from './Adaptive'

const GENERATION = '/api/admin/generation?hours=24'
const REFUSALS = '/api/admin/refusals?hours=24'
const POLL_MS = 60_000

const UNAVAILABLE = {
  concurrency: 'no free model slot',
  queued: 'waited too long for a slot',
  daily: 'daily call ceiling reached',
  connection: 'model API unreachable',
}

// `claude:unavailable:daily` -> "daily call ceiling reached"; an error keeps its class name.
function outcomeLabel(key) {
  const [, kind, detail] = key.split(':')
  if (kind === 'unavailable') return UNAVAILABLE[detail] || `refused (${detail})`
  if (kind === 'error') return `error: ${detail}`
  return kind
}

const STATUS_WORDS = {
  400: 'Bad request', 401: 'Not signed in', 403: 'Refused access', 405: 'Wrong method',
  409: 'Conflict', 410: 'Gone', 413: 'Body too large', 422: 'Invalid input',
  429: 'Too many requests', 500: 'Server error', 503: 'Temporarily unavailable',
}

// A read stopped at the row cap: the newest hours are missing, so the totals are low.
function Partial() {
  return (
    <p className="text-xs text-amber-800 dark:text-amber-300">
      Only part of this period could be read; the totals below are too low.
    </p>
  )
}

function useOps(path) {
  return useAdminRead(path, POLL_MS)
}

// Counter reads add two states of their own: an unread store, and a read cut short at the row cap.
function OpsState({ res, what, children }) {
  return (
    <ReadState res={res} what={what}>
      {data => (
        <>
          {!data.retrieved && (
            <Unread what={`Stored ${what}`}
                    note="This is not the same as there being none. Counts held by this server process are shown if any." />
          )}
          {data.retrieved && data.complete === false && <Partial />}
          {children(data)}
        </>
      )}
    </ReadState>
  )
}

function sum(values) {
  return values.reduce((a, b) => a + b, 0)
}

function generationTotals(d) {
  const entries = Object.entries(d.outcomes || {})
  const ok = sum(entries.filter(([k]) => k.endsWith(':ok')).map(([, n]) => n))
  const failed = sum(entries.filter(([k]) => !k.endsWith(':ok')).map(([, n]) => n))
  const q = d.questions || {}
  return { ok, failed, served: (q['served:inline'] || 0) + (q['served:queue'] || 0) }
}

// Only Claude calls have a ceiling; under Ollama the payload sends none.
function CeilingTile({ ceiling }) {
  if (!ceiling) return null
  return <Tile label="Daily call ceiling" value={`${ceiling.used} / ${ceiling.limit}`} hint={ceiling.scope} />
}

function DetailLink() {
  return (
    <Link to="/admin/engine" className="text-xs font-semibold text-slate-700 dark:text-slate-300 underline">
      Generation and refusals in detail
    </Link>
  )
}

/** Overview's card: model calls in the last 24 hours and the daily ceiling. */
export function GenerationSummary() {
  const res = useOps(GENERATION)
  return (
    <div className="space-y-2">
      <OpsState res={res} what="generation counts">
        {data => {
          const { ok, failed, served } = generationTotals(data)
          return (
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
              <Tile label="Questions served, 24 h" value={served} />
              <Tile label="Model calls ok" value={ok} />
              <Tile label="Model calls failed" value={failed} />
              <CeilingTile ceiling={data.daily_ceiling} />
            </div>
          )
        }}
      </OpsState>
      <DetailLink />
    </div>
  )
}

/** Overview's card: refusals in this hour's and the previous hour's buckets. */
export function RefusalSummary() {
  const res = useOps(REFUSALS)
  return (
    <div className="space-y-2">
      <OpsState res={res} what="refusal counts">
        {data => {
          const recent = data.recent || []
          return recent.length === 0
            ? <p className="text-sm text-gray-600 dark:text-gray-400">No refused requests this hour or last.</p>
            : <RefusalTable rows={recent.slice(0, 5)} />
        }}
      </OpsState>
      <DetailLink />
    </div>
  )
}

function RefusalTable({ rows }) {
  return (
    <div className="overflow-x-auto rounded-xl border border-gray-100 dark:border-gray-800 bg-white dark:bg-gray-900">
      <table className="w-full text-sm">
        <thead>
          <tr className="text-left text-xs uppercase text-gray-600 dark:text-gray-400">
            <th className="px-3 py-2">Status</th><th className="px-3 py-2">Route</th>
            <th className="px-3 py-2 text-right">Count</th>
          </tr>
        </thead>
        <tbody>
          {rows.map(r => (
            <tr key={`${r.status} ${r.method} ${r.route}`} className="border-t border-gray-100 dark:border-gray-800">
              <td className="px-3 py-2 text-gray-900 dark:text-white">
                {r.status} <span className="text-gray-600 dark:text-gray-400">{STATUS_WORDS[r.status] || ''}</span>
              </td>
              <td className="px-3 py-2 font-mono text-xs text-gray-900 dark:text-white break-all">{r.method} {r.route}</td>
              <td className="px-3 py-2 text-right tabular-nums text-gray-900 dark:text-white">{r.count}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function GenerationDetail({ data }) {
  const { ok, failed, served } = generationTotals(data)
  const failures = Object.entries(data.outcomes || {}).filter(([k]) => !k.endsWith(':ok'))
  const waits = Object.entries(data.waits || {})
  const tokens = data.tokens || {}
  const q = data.questions || {}
  const nothing = ok + failed === 0 && served === 0

  return (
    <div className="space-y-3">
      {data.retrieved && nothing && (
        <p className="text-sm text-gray-600 dark:text-gray-400">No model calls recorded in the last 24 hours.</p>
      )}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        <Tile label="Questions served" value={served}
              hint={`${q['served:queue'] || 0} from the queue, ${q['served:inline'] || 0} made on the spot`} />
        <Tile label="Model calls ok" value={ok} />
        <Tile label="Model calls failed" value={failed} />
        <CeilingTile ceiling={data.daily_ceiling} />
        {waits.map(([provider, w]) => (
          <Tile key={provider} label={`Mean wait (${provider})`}
                value={w.mean_ms == null ? '—' : `${(w.mean_ms / 1000).toFixed(1)} s`}
                hint={w.max_ms == null ? null : `longest ${(w.max_ms / 1000).toFixed(1)} s, includes queueing`} />
        ))}
        {data.provider === 'claude' && (
          <Tile label="Estimated cost" value={`$${data.estimated_cost_usd.toFixed(2)}`}
                hint={`${tokens['claude:in'] || 0} in / ${tokens['claude:out'] || 0} out tokens at list price`} />
        )}
      </div>
      {failures.length > 0 && (
        <ul className="text-sm space-y-1">
          {failures.map(([key, n]) => (
            <li key={key} className="text-gray-900 dark:text-white">
              <span className="tabular-nums font-bold">{n}</span> × {outcomeLabel(key)}
            </li>
          ))}
        </ul>
      )}
      {(q.prefetch_failed || q.prefetch_discarded) ? (
        <p className="text-xs text-gray-600 dark:text-gray-400">
          Prefetch: {q.prefetched || 0} queued, {q.prefetch_failed || 0} failed,
          {' '}{q.prefetch_discarded || 0} discarded because their lesson ended or its queue was full.
        </p>
      ) : null}
      {data.dropped > 0 && (
        <p className="text-xs text-amber-800 dark:text-amber-300">
          {data.dropped} counter cells were dropped while the database was unreachable; totals are low.
        </p>
      )}
    </div>
  )
}

function RefusalDetail({ data }) {
  const rows = data.refusals || []
  if (data.retrieved && rows.length === 0) {
    return <p className="text-sm text-gray-600 dark:text-gray-400">No refused requests in the last 24 hours.</p>
  }
  return rows.length > 0 ? <RefusalTable rows={rows} /> : null
}

function Generation() {
  const res = useOps(GENERATION)
  return (
    <div className="space-y-3">
      <OpsState res={res} what="generation counts">
        {data => <GenerationDetail data={data} />}
      </OpsState>
    </div>
  )
}

function Refusals() {
  const res = useOps(REFUSALS)
  return (
    <div className="space-y-3">
      <OpsState res={res} what="refusal counts">
        {data => <RefusalDetail data={data} />}
      </OpsState>
    </div>
  )
}

export default function AdminEngine() {
  return (
    <div className="p-6 space-y-8 max-w-6xl">
      <header>
        <h1 className="text-2xl font-black text-gray-900 dark:text-white">Engine</h1>
        <p className="text-sm text-gray-600 dark:text-gray-400">
          The last 24 hours, in hourly counts. Never a student, an address or a question&rsquo;s content.
        </p>
      </header>
      <section className="space-y-3">
        <h2 className="text-sm font-black uppercase tracking-wide text-gray-600 dark:text-gray-400">Question generation</h2>
        <Generation />
      </section>
      <section className="space-y-3">
        <h2 className="text-sm font-black uppercase tracking-wide text-gray-600 dark:text-gray-400">Refused requests</h2>
        <p className="text-xs text-gray-600 dark:text-gray-400">
          Every 4xx and 5xx answer except 404, by route. A 503 on question generation is a bound working, not an outage.
        </p>
        <Refusals />
      </section>
      <section className="space-y-3">
        <h2 className="text-sm font-black uppercase tracking-wide text-gray-600 dark:text-gray-400">Signal quality</h2>
        <SignalQuality />
      </section>
      <section className="space-y-3">
        <h2 className="text-sm font-black uppercase tracking-wide text-gray-600 dark:text-gray-400">Adaptive decisions</h2>
        <AdaptiveDecisions />
      </section>
    </div>
  )
}
