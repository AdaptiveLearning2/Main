import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { CheckCircle2, AlertTriangle, HelpCircle, Search, ExternalLink } from 'lucide-react'
import { apiFetch } from '../../lib/api'

const STATUS = {
  ok:       { Icon: CheckCircle2,  cls: 'text-emerald-600 dark:text-emerald-400' },
  degraded: { Icon: AlertTriangle, cls: 'text-amber-600 dark:text-amber-400' },
  // A check that could not run is `unknown`, never `ok` — a checkmark would
  // wrongly claim it passed.
  unknown:  { Icon: HelpCircle,    cls: 'text-gray-400 dark:text-gray-500' },
}

const CHECK_LABELS = {
  eeg_sidecar:        'EEG sidecar',
  ingest_mode:        'Ingest mode',
  school_year:        'School year',
  last_rollup:        'Last rollup',
  consent_enforcement:'Consent enforcement',
}

function HealthStrip() {
  const [checks, setChecks] = useState(null)
  const [failed, setFailed] = useState(false)

  useEffect(() => {
    apiFetch('/api/admin/health')
      .then(d => setChecks(d.checks || []))
      .catch(() => setFailed(true))
  }, [])

  if (failed) return <p className="text-sm text-gray-500">Could not read system health.</p>
  if (!checks) return <p className="text-sm text-gray-400">Checking…</p>

  return (
    <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-5 gap-3">
      {checks.map(c => {
        const { Icon, cls } = STATUS[c.status] || STATUS.unknown
        return (
          <div key={c.key} className="bg-white dark:bg-gray-900 border border-gray-100 dark:border-gray-800 rounded-xl px-4 py-3">
            <div className="flex items-center gap-2">
              <Icon size={16} className={cls} />
              <p className="text-xs font-bold text-gray-900 dark:text-white">
                {CHECK_LABELS[c.key] || c.key}
              </p>
            </div>
            <p className="mt-1 text-xs text-gray-500 dark:text-gray-400 break-words">{c.detail}</p>
          </div>
        )
      })}
    </div>
  )
}

function ConsentCounts() {
  const [data, setData] = useState(null)
  const [failed, setFailed] = useState(false)

  useEffect(() => {
    apiFetch('/api/admin/consent-summary')
      .then(d => (d.retrieved ? setData(d) : setFailed(true)))
      .catch(() => setFailed(true))
  }, [])

  if (failed) return <p className="text-sm text-gray-500">Could not read consent counts.</p>
  if (!data) return <p className="text-sm text-gray-400">Loading…</p>

  const tiles = [
    ['Students', data.students],
    ['EEG', data.eeg],
    ['Headband heart', data.headband_optical],
    ['Camera', data.camera],
    ['Awaiting student ack', data.awaiting_student_ack],
  ]

  return (
    <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-3">
      {tiles.map(([label, value]) => (
        <div key={label} className="bg-white dark:bg-gray-900 border border-gray-100 dark:border-gray-800 rounded-xl px-4 py-3">
          <p className="text-2xl font-black text-gray-900 dark:text-white">{value}</p>
          <p className="text-xs text-gray-500 dark:text-gray-400 font-semibold">{label}</p>
        </div>
      ))}
    </div>
  )
}

function StudentSearch() {
  const [term, setTerm] = useState('')
  // Holds the query its results belong to. `searching`/`results` are derived
  // by comparing it to the current box value, so a late response can't be
  // rendered against a query the user has since changed.
  const [hits, setHits] = useState({ q: '', students: [] })

  const q = term.trim()
  const enough = q.length >= 2
  const searching = enough && hits.q !== q
  const results = enough && hits.q === q ? hits.students : []

  useEffect(() => {
    if (!enough) return
    // Debounced, since every keystroke would otherwise fire a query.
    let cancelled = false
    const t = setTimeout(() => {
      apiFetch(`/api/admin/students/search?q=${encodeURIComponent(q)}`)
        // On failure, record the query with no results rather than leaving
        // the previous query's hits on screen.
        .then(d => { if (!cancelled) setHits({ q, students: d.students || [] }) })
        .catch(() => { if (!cancelled) setHits({ q, students: [] }) })
    }, 300)
    return () => { cancelled = true; clearTimeout(t) }
  }, [q, enough])

  return (
    <div>
      <div className="relative">
        <Search size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400" />
        <input
          value={term}
          onChange={e => setTerm(e.target.value)}
          placeholder="Find a student by name or email…"
          className="w-full pl-9 pr-3 py-2.5 rounded-xl border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900 text-sm text-gray-900 dark:text-white focus:outline-none focus:ring-2 focus:ring-slate-400"
        />
      </div>
      {searching && <p className="mt-2 text-xs text-gray-400">Searching…</p>}
      {results.length > 0 && (
        <ul className="mt-3 space-y-2">
          {results.map(s => (
            <li key={s.id}>
              <Link
                to={`/teacher/students/${s.id}/report`}
                className="flex items-center justify-between gap-3 bg-white dark:bg-gray-900 border border-gray-100 dark:border-gray-800 rounded-xl px-4 py-2.5 hover:border-slate-400 transition"
              >
                <span className="min-w-0">
                  <span className="block text-sm font-bold text-gray-900 dark:text-white truncate">{s.display_name}</span>
                  <span className="block text-xs text-gray-500 truncate">{s.email}</span>
                </span>
                <ExternalLink size={14} className="text-gray-400 flex-shrink-0" />
              </Link>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

export default function AdminOverview() {
  return (
    <div className="p-6 space-y-8 max-w-6xl">
      <div>
        <h1 className="text-2xl font-black text-gray-900 dark:text-white">Admin console</h1>
        <p className="text-sm text-gray-500 dark:text-gray-400">
          Platform-wide settings for this school.
        </p>
      </div>

      <section className="space-y-3">
        <h2 className="text-sm font-black uppercase tracking-wide text-gray-500">System health</h2>
        <HealthStrip />
      </section>

      <section className="space-y-3">
        <h2 className="text-sm font-black uppercase tracking-wide text-gray-500">Consent</h2>
        <ConsentCounts />
        <p className="text-xs text-gray-400">
          Counts only. Which student agreed to what is not shown here.
        </p>
      </section>

      <section className="space-y-3">
        <h2 className="text-sm font-black uppercase tracking-wide text-gray-500">Find a student</h2>
        <StudentSearch />
      </section>
    </div>
  )
}
