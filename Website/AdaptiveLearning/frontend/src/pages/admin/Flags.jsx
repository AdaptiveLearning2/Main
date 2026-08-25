import { useCallback, useEffect, useState } from 'react'
import useValueChange from '../../hooks/useValueChange'
import { AlertTriangle, History, Lock } from 'lucide-react'
import { apiFetch } from '../../lib/api'
import useAdminResource from '../../hooks/useAdminResource'
import Toggle from '../../components/ui/Toggle'

const CONSENT_FLAG = 'consent_enforcement_enabled'

const GROUPS = [
  { title: 'Learning strategies', keys: ['strategy_llm_enabled'] },
  {
    title: 'Recording',
    keys: ['recording_eeg_enabled', 'recording_heart_enabled', 'recording_camera_enabled'],
    note: 'These can withhold recording. They can never grant it — each is combined with the student’s own consent.',
  },
]

const LABELS = {
  strategy_llm_enabled:     'Model pass for learning strategies',
  recording_eeg_enabled:    'EEG recording',
  recording_heart_enabled:  'Headband heart rate',
  recording_camera_enabled: 'Camera',
  [CONSENT_FLAG]:           'Consent enforcement',
}

const BYPASS_CHOICES = [15, 30, 60, 120, 240]


function FlagHistory({ flagKey }) {
  const [open, setOpen] = useState(false)
  const [data, setData] = useState(null)

  useEffect(() => {
    if (!open) return
    apiFetch(`/api/admin/flags/${flagKey}/history`)
      .then(setData)
      .catch(() => setData({ retrieved: false, changes: [] }))
  }, [open, flagKey])

  return (
    <div className="mt-2">
      <button
        onClick={() => setOpen(o => !o)}
        className="flex items-center gap-1.5 text-xs font-semibold text-gray-500 hover:text-gray-800 dark:hover:text-gray-200 dark:text-gray-400"
      >
        <History size={12} /> {open ? 'Hide history' : 'History'}
      </button>
      {open && (
        <div className="mt-2 text-xs">
          {!data && <p className="text-gray-600 dark:text-gray-400">Loading…</p>}
          {data && data.retrieved === false && (
            <p className="text-gray-500 dark:text-gray-400">Could not read the history for this flag.</p>
          )}
          {data?.retrieved && data.changes.length === 0 && (
            <p className="text-gray-500 dark:text-gray-400">Never changed.</p>
          )}
          {data?.retrieved && data.changes.length > 0 && (
            <ul className="space-y-1">
              {data.changes.map((c, i) => (
                <li key={i} className="text-gray-600 dark:text-gray-400">
                  <span className="font-semibold">{c.new_enabled ? 'on' : 'off'}</span>
                  {' — '}{c.changed_by}{' · '}
                  {new Date(c.changed_at).toLocaleString()}
                  {c.bypass_until && ` · until ${new Date(c.bypass_until).toLocaleTimeString()}`}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  )
}

function ConsentPanel({ flag, active, onSet, busy }) {
  const [minutes, setMinutes] = useState(30)
  const [ack, setAck] = useState(false)

  // Clear the checkbox when enforcement resumes, so it doesn't carry over
  // into a later bypass. Done during render, not in an effect, so it never
  // paints still ticked for a frame.
  useValueChange(active, next => { if (next) setAck(false) })

  const until = flag?.bypass_until ? new Date(flag.bypass_until) : null

  return (
    <div className="rounded-2xl border-2 border-rose-300 dark:border-rose-800 bg-rose-50/60 dark:bg-rose-950/30 p-5">
      <div className="flex items-start gap-3">
        <AlertTriangle className="text-rose-600 flex-shrink-0 mt-0.5" size={20} />
        <div className="min-w-0 flex-1">
          <h3 className="font-black text-gray-900 dark:text-white">{LABELS[CONSENT_FLAG]}</h3>
          {active ? (
            <p className="text-sm text-gray-700 dark:text-gray-300 mt-1">
              Consent is enforced. Nothing is recorded for a student who has not agreed to it.
            </p>
          ) : (
            <p className="text-sm font-bold text-rose-700 dark:text-rose-300 mt-1">
              Bypassed — signals are being recorded from students who have not consented.
              {until && <> Enforcement resumes automatically at {until.toLocaleTimeString()}.</>}
            </p>
          )}

          {active ? (
            <div className="mt-4 space-y-3">
              <div>
                <label className="block text-xs font-bold text-gray-600 dark:text-gray-400 mb-1">
                  Bypass for
                </label>
                <select
                  value={minutes}
                  onChange={e => setMinutes(Number(e.target.value))}
                  className="rounded-lg border border-gray-300 dark:border-gray-700 bg-white dark:bg-gray-900 px-3 py-1.5 text-sm"
                >
                  {BYPASS_CHOICES.map(m => (
                    <option key={m} value={m}>{m} minutes</option>
                  ))}
                </select>
                <p className="mt-1 text-xs text-gray-500 dark:text-gray-400">
                  It turns itself back on when the time is up. There is no indefinite option.
                </p>
              </div>

              <label className="flex items-start gap-2 text-sm text-gray-700 dark:text-gray-300">
                <input
                  type="checkbox"
                  checked={ack}
                  onChange={e => setAck(e.target.checked)}
                  className="mt-0.5"
                />
                <span>
                  I understand this records EEG, heart-rate and camera signals from students
                  who have not consented, and that it is for prototyping only.
                </span>
              </label>

              <button
                disabled={!ack || busy}
                onClick={() => onSet(CONSENT_FLAG, false, minutes)}
                className="px-4 py-2 rounded-xl bg-rose-600 text-white text-sm font-bold disabled:opacity-40 hover:bg-rose-700 transition"
              >
                Bypass consent for {minutes} minutes
              </button>
            </div>
          ) : (
            <button
              disabled={busy}
              onClick={() => onSet(CONSENT_FLAG, true)}
              className="mt-4 flex items-center gap-2 px-4 py-2 rounded-xl bg-emerald-600 text-white text-sm font-bold disabled:opacity-40 hover:bg-emerald-700 transition"
            >
              <Lock size={14} /> Re-enable consent enforcement now
            </button>
          )}

          <FlagHistory flagKey={CONSENT_FLAG} />
        </div>
      </div>
    </div>
  )
}

export default function AdminFlags() {
  const [env, setEnv] = useState([])

  // Poll every 30s: the bypass expires on the clock, not on a write, so
  // without polling the banner would keep claiming a bypass that's over.
  const { data, busy, error, mutate } = useAdminResource({
    load: useCallback(() => apiFetch('/api/admin/flags'), []),
    pollMs: 30_000,
  })
  const flags = data?.flags ?? null
  const active = data?.consent_enforcement_active ?? true

  // Separate from the hook's poll: a failure here just empties this list,
  // not the whole page.
  useEffect(() => {
    apiFetch('/api/admin/env-flags').then(d => setEnv(d.flags || [])).catch(() => setEnv([]))
  }, [])

  const set = (key, enabled, bypassMinutes) => mutate(() =>
    apiFetch(`/api/admin/flags/${key}`, {
      method: 'PUT',
      body: { enabled, bypass_minutes: bypassMinutes ?? null },
    }))

  if (error && !flags) return <div className="p-6 text-sm text-rose-600">{error}</div>
  if (!flags) return <div className="p-6 text-sm text-gray-600 dark:text-gray-400">Loading…</div>

  const byKey = Object.fromEntries(flags.map(f => [f.key, f]))

  return (
    <div className="p-6 space-y-8 max-w-4xl">
      <div>
        <h1 className="text-2xl font-black text-gray-900 dark:text-white">Feature flags</h1>
        <p className="text-sm text-gray-500 dark:text-gray-400">
          Global switches. They take effect within about half a minute — no redeploy.
        </p>
      </div>

      {error && <p className="text-sm text-rose-600">{error}</p>}

      {GROUPS.map(group => (
        <section key={group.title} className="space-y-3">
          <h2 className="text-sm font-black uppercase tracking-wide text-gray-500 dark:text-gray-400">{group.title}</h2>
          {group.note && <p className="text-xs text-gray-500 dark:text-gray-400">{group.note}</p>}
          <div className="space-y-2">
            {group.keys.map(key => {
              const flag = byKey[key]
              if (!flag) return null
              return (
                <div key={key} className="bg-white dark:bg-gray-900 border border-gray-100 dark:border-gray-800 rounded-xl px-4 py-3">
                  <div className="flex items-center justify-between gap-4">
                    <div className="min-w-0">
                      <p className="text-sm font-bold text-gray-900 dark:text-white">{LABELS[key] || key}</p>
                      {flag.description && (
                        <p className="text-xs text-gray-500 dark:text-gray-400 mt-0.5">{flag.description}</p>
                      )}
                      {flag.is_default && (
                        <p className="text-xs text-gray-600 mt-0.5 dark:text-gray-400">Never set — using the built-in default.</p>
                      )}
                    </div>
                    <Toggle
                      tone="emerald"
                      checked={flag.enabled}
                      disabled={busy}
                      onChange={v => set(key, v)}
                    />
                  </div>
                  <FlagHistory flagKey={key} />
                </div>
              )
            })}
          </div>
        </section>
      ))}

      <section className="space-y-3">
        <h2 className="text-sm font-black uppercase tracking-wide text-rose-600">Danger zone</h2>
        <ConsentPanel flag={byKey[CONSENT_FLAG]} active={active} onSet={set} busy={busy} />
      </section>

      <section className="space-y-3">
        <h2 className="text-sm font-black uppercase tracking-wide text-gray-500 dark:text-gray-400">Deployment flags</h2>
        <p className="text-xs text-gray-500 dark:text-gray-400">
          Read-only. These come from the environment and need a redeploy to change. Several belong
          to the EEG sidecar’s own environment, so a blank value here means “not set for the
          backend”, not “off”.
        </p>
        <div className="space-y-1.5">
          {env.map(f => (
            <div key={f.key} className="bg-gray-50 dark:bg-gray-900/60 border border-gray-100 dark:border-gray-800 rounded-xl px-4 py-2.5 flex items-center justify-between gap-4">
              <div className="min-w-0">
                <p className="text-xs font-mono font-bold text-gray-900 dark:text-white">{f.key}</p>
                <p className="text-xs text-gray-500 dark:text-gray-400">{f.description}</p>
              </div>
              <code className="text-xs font-bold text-gray-700 dark:text-gray-300 flex-shrink-0">
                {f.value === null || f.value === undefined ? '—' : String(f.value)}
              </code>
            </div>
          ))}
        </div>
      </section>
    </div>
  )
}
