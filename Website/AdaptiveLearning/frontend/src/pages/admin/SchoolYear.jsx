import { useCallback, useState } from 'react'
import { apiFetch } from '../../lib/api'
import useAdminResource from '../../hooks/useAdminResource'
import { isValidTimezone, knownTimezones } from '../../lib/timezone'

// The backend has six states because "not gating on a year" and "inside the
// year" both record but for different reasons, and an admin needs to know
// which one applies.
const STATE_COPY = {
  open:          ['Recording', 'Today is inside the configured school year.'],
  not_enforced:  ['Recording', 'The year is not being enforced. Consent still applies.'],
  before_year:   ['Not recording', 'The school year has not started yet.'],
  after_year:    ['Not recording', 'The school year has ended.'],
  unconfigured:  ['Not recording', 'No school year has been configured.'],
  unreadable:    ['Not recording', 'The window could not be read — check the timezone.'],
}

// Computed once per module, not per render: it's ~450 strings and doesn't
// change while the tab is open.
const ZONES = knownTimezones()

export default function AdminSchoolYear() {
  // The user's unsaved edits, or `null` for "showing what the server said".
  const [draft, setDraft] = useState(null)

  // Route every edit through this, not `setDraft` directly, so "Saved."
  // can't linger once the admin starts typing a new, unsaved change.
  const edit = (patch) => {
    setSaved(false)
    setDraft(patch)
  }
  const [saved, setSaved] = useState(false)

  const { data, busy, error, mutate } = useAdminResource({
    load: useCallback(() => apiFetch('/api/admin/retention-window'), []),
  })

  // Derived, not synced via an effect, to avoid an extra render each time the
  // server payload changes. The draft wins while it exists; clearing it after
  // a save re-derives the form from what the server actually stored.
  //
  // `enforced !== false` so a row missing the column, or one PostgREST returns
  // without it, reads as enforced rather than as the gate being off.
  const form = draft ?? (data && {
    enforced: data.enforced !== false,
    starts_on: data.starts_on || '',
    ends_on: data.ends_on || '',
    timezone: data.timezone || 'UTC',
  })

  // The backend denies rather than falling back on an unresolvable timezone,
  // so a typo here would silently stop recording for every student.
  const save = async () => {
    setSaved(false)
    const ok = await mutate(() =>
      apiFetch('/api/admin/retention-window', { method: 'PUT', body: form }))
    // Drop the draft so the form re-derives from the saved row: the endpoint
    // clamps values, so the stored row is the authority, not what was typed.
    if (ok) { setDraft(null); setSaved(true) }
  }

  if (error && !data) return <div className="p-6 text-sm text-rose-600">{error}</div>
  if (!data || !form) return <div className="p-6 text-sm text-gray-400">Loading…</div>

  const tzValid = isValidTimezone(form.timezone)

  const [headline, detail] = STATE_COPY[data.state] || ['Unknown', data.state]
  const recording = data.state === 'open' || data.state === 'not_enforced'

  return (
    <div className="p-6 space-y-6 max-w-2xl">
      <div>
        <h1 className="text-2xl font-black text-gray-900 dark:text-white">School year</h1>
        <p className="text-sm text-gray-500 dark:text-gray-400">
          Outside the year nothing is recorded, whatever consent says.
        </p>
      </div>

      <div className={`rounded-xl px-4 py-3 border ${recording
        ? 'bg-emerald-50 dark:bg-emerald-950/30 border-emerald-200 dark:border-emerald-800'
        : 'bg-amber-50 dark:bg-amber-950/30 border-amber-200 dark:border-amber-800'}`}>
        <p className="font-bold text-sm text-gray-900 dark:text-white">{headline}</p>
        <p className="text-xs text-gray-600 dark:text-gray-400">{detail}</p>
      </div>

      {error && <p className="text-sm text-rose-600">{error}</p>}
      {saved && <p className="text-sm text-emerald-600">Saved.</p>}

      <div className="space-y-4 bg-white dark:bg-gray-900 border border-gray-100 dark:border-gray-800 rounded-xl p-5">
        <label className="flex items-start gap-2">
          <input
            type="checkbox"
            checked={form.enforced}
            onChange={e => edit({ ...form, enforced: e.target.checked })}
            className="mt-1"
          />
          <span>
            <span className="block text-sm font-bold text-gray-900 dark:text-white">Enforce term dates</span>
            <span className="block text-xs text-gray-500 dark:text-gray-400">
              Off means recording is not gated on a term at all — for prototyping, or a deployment
              that does not run on one. Consent is unaffected either way.
            </span>
          </span>
        </label>

        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
          <div>
            <label htmlFor="school-year-starts-on"
                   className="block text-xs font-bold text-gray-600 dark:text-gray-400 mb-1">Starts on</label>
            <input
              id="school-year-starts-on"
              type="date"
              value={form.starts_on}
              disabled={!form.enforced}
              onChange={e => edit({ ...form, starts_on: e.target.value })}
              className="w-full rounded-lg border border-gray-300 dark:border-gray-700 bg-white dark:bg-gray-900 px-3 py-2 text-sm disabled:opacity-40"
            />
          </div>
          <div>
            <label htmlFor="school-year-ends-on"
                   className="block text-xs font-bold text-gray-600 dark:text-gray-400 mb-1">Ends on</label>
            <input
              id="school-year-ends-on"
              type="date"
              value={form.ends_on}
              disabled={!form.enforced}
              onChange={e => edit({ ...form, ends_on: e.target.value })}
              className="w-full rounded-lg border border-gray-300 dark:border-gray-700 bg-white dark:bg-gray-900 px-3 py-2 text-sm disabled:opacity-40"
            />
          </div>
        </div>

        <div>
          <label htmlFor="school-timezone"
                 className="block text-xs font-bold text-gray-600 dark:text-gray-400 mb-1">Timezone</label>
          <input
            id="school-timezone"
            list="school-timezone-options"
            value={form.timezone}
            onChange={e => edit({ ...form, timezone: e.target.value })}
            placeholder="America/Chicago"
            aria-invalid={!tzValid}
            aria-describedby="school-timezone-hint"
            className={`w-full rounded-lg border bg-white dark:bg-gray-900 px-3 py-2 text-sm ${
              tzValid
                ? 'border-gray-300 dark:border-gray-700'
                : 'border-rose-400 dark:border-rose-600'
            }`}
          />
          {/* Suggestions only, not the validator — an empty list here just
              means no autocomplete, not a broken field. */}
          <datalist id="school-timezone-options">
            {ZONES.map(z => <option key={z} value={z} />)}
          </datalist>
          {!tzValid && (
            <p className="mt-1 text-xs font-bold text-rose-600 dark:text-rose-400">
              Not a timezone this platform can resolve. Saving it would stop
              recording for every student until it is corrected.
            </p>
          )}
          <p id="school-timezone-hint" className="mt-1 text-xs text-gray-500">
            The school’s own zone. It sets both the term boundaries and the day buckets on every
            report, so a wrong one moves a lesson onto the wrong day of a parent’s chart.
          </p>
        </div>

        <button
          onClick={save}
          disabled={busy || !tzValid}
          className="px-4 py-2 rounded-xl bg-slate-700 text-white text-sm font-bold disabled:opacity-40 hover:bg-slate-800 transition"
        >
          Save
        </button>
      </div>
    </div>
  )
}
