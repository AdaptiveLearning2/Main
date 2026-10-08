import { useCallback } from 'react'
import { Link } from 'react-router-dom'
import { apiFetch } from '../../lib/api'
import useAdminResource from '../../hooks/useAdminResource'
import LoadError from '../../components/ui/LoadError'

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

function Tile({ label, value, hint }) {
  return (
    <div className="bg-white dark:bg-gray-900 border border-gray-100 dark:border-gray-800 rounded-xl px-4 py-3">
      <p className="text-2xl font-black text-gray-900 dark:text-white">{value}</p>
      <p className="text-xs text-gray-600 dark:text-gray-400 font-semibold">{label}</p>
      {hint && <p className="mt-0.5 text-xs text-gray-600 dark:text-gray-400">{hint}</p>}
    </div>
  )
}

// The request worked and the read inside it did not: never drawn as "nothing happened".
function Unread({ what }) {
  return (
    <div className="rounded-xl border border-amber-200 bg-amber-50 p-4 dark:border-amber-900 dark:bg-amber-950/40">
      <p className="font-bold text-amber-900 dark:text-amber-200">{what} could not be read.</p>
      <p className="mt-1 text-sm text-amber-800 dark:text-amber-300">
        This is not the same as there being none. Counts held by this server process are shown if any.
      </p>
    </div>
  )
}

function useOps(path) {
  const load = useCallback(() => apiFetch(path), [path])
  return useAdminResource({ load, pollMs: POLL_MS })
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

/** Overview's card: model calls today and the daily ceiling. */
export function GenerationSummary() {
  const { data, error } = useOps(GENERATION)
  if (error) return <LoadError error={error} />
  if (!data) return <p className="text-sm text-gray-600 dark:text-gray-400">Loading…</p>
  const { ok, failed, served } = generationTotals(data)
  const ceiling = data.daily_ceiling
  return (
    <div className="space-y-2">
      {!data.retrieved && <Unread what="Stored generation counts" />}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        <Tile label="Questions served, 24 h" value={served} />
        <Tile label="Model calls ok" value={ok} />
        <Tile label="Model calls failed" value={failed} />
        <Tile label="Daily call ceiling" value={`${ceiling.used} / ${ceiling.limit}`}
              hint={ceiling.scope} />
      </div>
      <Link to="/admin/engine" className="text-xs font-semibold text-slate-700 dark:text-slate-300 underline">
        Generation and refusals in detail
      </Link>
    </div>
  )
}

/** Overview's card: refusals in this hour's and the previous hour's buckets. */
export function RefusalSummary() {
  const { data, error } = useOps(REFUSALS)
  if (error) return <LoadError error={error} />
  if (!data) return <p className="text-sm text-gray-600 dark:text-gray-400">Loading…</p>
  const recent = data.recent || []
  return (
    <div className="space-y-2">
      {!data.retrieved && <Unread what="Stored refusal counts" />}
      {recent.length === 0
        ? <p className="text-sm text-gray-600 dark:text-gray-400">No refused requests this hour or last.</p>
        : <RefusalTable rows={recent.slice(0, 5)} />}
      <Link to="/admin/engine" className="text-xs font-semibold text-slate-700 dark:text-slate-300 underline">
        Generation and refusals in detail
      </Link>
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

function Generation() {
  const { data, error } = useOps(GENERATION)
  if (error) return <LoadError error={error} />
  if (!data) return <p className="text-sm text-gray-600 dark:text-gray-400">Loading…</p>

  const { ok, failed, served } = generationTotals(data)
  const failures = Object.entries(data.outcomes || {}).filter(([k]) => !k.endsWith(':ok'))
  const waits = Object.entries(data.waits || {})
  const tokens = data.tokens || {}
  const q = data.questions || {}
  const nothing = ok + failed === 0 && served === 0

  return (
    <div className="space-y-3">
      {!data.retrieved && <Unread what="Stored generation counts" />}
      {data.retrieved && nothing && (
        <p className="text-sm text-gray-600 dark:text-gray-400">No model calls recorded in the last 24 hours.</p>
      )}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        <Tile label="Questions served" value={served}
              hint={`${q['served:queue'] || 0} from the queue, ${q['served:inline'] || 0} made on the spot`} />
        <Tile label="Model calls ok" value={ok} />
        <Tile label="Model calls failed" value={failed} />
        <Tile label="Daily call ceiling" value={`${data.daily_ceiling.used} / ${data.daily_ceiling.limit}`}
              hint={data.daily_ceiling.scope} />
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
          {' '}{q.prefetch_discarded || 0} made for a lesson that had ended.
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

function Refusals() {
  const { data, error } = useOps(REFUSALS)
  if (error) return <LoadError error={error} />
  if (!data) return <p className="text-sm text-gray-600 dark:text-gray-400">Loading…</p>
  const rows = data.refusals || []
  return (
    <div className="space-y-3">
      {!data.retrieved && <Unread what="Stored refusal counts" />}
      {data.retrieved && rows.length === 0
        ? <p className="text-sm text-gray-600 dark:text-gray-400">No refused requests in the last 24 hours.</p>
        : rows.length > 0 && <RefusalTable rows={rows} />}
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
    </div>
  )
}
