import { useEffect, useState } from 'react'
import { apiFetch } from '../../lib/api'
import FlowDot from './FlowDot'

const POLL_MS = 5_000

export default function AdminLiveFlow() {
  const [data, setData] = useState(null)
  const [failed, setFailed] = useState(false)

  useEffect(() => {
    let cancelled = false
    const tick = () => apiFetch('/api/admin/live-signals')
      .then(d => { if (!cancelled) { setData(d); setFailed(!d.retrieved) } })
      .catch(() => { if (!cancelled) setFailed(true) })
    tick()
    const t = setInterval(tick, POLL_MS)
    return () => { cancelled = true; clearInterval(t) }
  }, [])

  return (
    <div className="p-6 space-y-6 max-w-4xl">
      <div>
        <h1 className="text-2xl font-black text-gray-900 dark:text-white">Data flow</h1>
        <p className="text-sm text-gray-500 dark:text-gray-400">
          Whether signals are arriving for each open session. Not what they say — no readings,
          values or emotion labels are sent to this page.
        </p>
      </div>

      <div className="flex flex-wrap gap-4 text-xs text-gray-500 dark:text-gray-400">
        <span className="flex items-center gap-1.5"><span className="h-2.5 w-2.5 rounded-full bg-emerald-500" /> receiving</span>
        <span className="flex items-center gap-1.5"><span className="h-2.5 w-2.5 rounded-full bg-slate-400" /> quiet ({'>'}90s)</span>
        <span className="flex items-center gap-1.5"><span className="h-2.5 w-2.5 rounded-full bg-amber-500" /> stale ({'>'}10m)</span>
        <span className="flex items-center gap-1.5"><span className="h-2.5 w-2.5 rounded-full border-2 border-gray-300 dark:border-gray-600" /> never reported</span>
      </div>

      {failed && <p className="text-sm text-rose-600">Could not read the open sessions.</p>}
      {!data && !failed && <p className="text-sm text-gray-600 dark:text-gray-400">Loading…</p>}

      {data?.retrieved && data.sessions.length === 0 && (
        <p className="text-sm text-gray-500 dark:text-gray-400">No sessions are open right now.</p>
      )}

      {data?.capped && (
        <p className="text-sm text-amber-600">
          Showing the most recent sessions only — the cap was reached, which is unexpected for
          one school and worth looking into.
        </p>
      )}

      <div className="space-y-2">
        {data?.sessions?.map(s => (
          <div
            key={s.session_id}
            className="bg-white dark:bg-gray-900 border border-gray-100 dark:border-gray-800 rounded-xl px-4 py-3 flex items-center justify-between gap-4"
          >
            <div className="min-w-0">
              <p className="text-sm font-bold text-gray-900 dark:text-white truncate">{s.student_name}</p>
              <p className="text-xs text-gray-500 dark:text-gray-400">
                started {s.started_at ? new Date(s.started_at).toLocaleTimeString() : 'unknown'}
              </p>
            </div>
            <div className="flex items-center gap-5 flex-shrink-0">
              <FlowDot channel={s.eeg} label="EEG" />
              <FlowDot channel={s.camera} label="Camera" />
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}
