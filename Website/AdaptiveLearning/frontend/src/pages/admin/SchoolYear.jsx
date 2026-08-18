import { useCallback, useState } from 'react'
import { apiFetch } from '../../lib/api'
import useAdminResource from '../../hooks/useAdminResource'
import { isValidTimezone, knownTimezones } from '../../lib/timezone'

// What each state means to a person. The backend keeps six apart on purpose --
// "not gating on a year" and "inside the year" both record, for different
// reasons, and an admin looking at this page needs to know which one they have.
const STATE_COPY = {
  open:          ['Recording', 'Today is inside the configured school year.'],
  not_enforced:  ['Recording', 'The year is not being enforced. Consent still applies.'],
  before_year:   ['Not recording', 'The school year has not started yet.'],
  after_year:    ['Not recording', 'The school year has ended.'],
  unconfigured:  ['Not recording', 'No school year has been configured.'],
  unreadable:    ['Not recording', 'The window could not be read — check the timezone.'],
}

// Once per module rather than per render: the list is ~450 strings and does
// not change while the tab is open.
const ZONES = knownTimezones()

export default function AdminSchoolYear() {
  // The user's unsaved edits, or `null` for "showing what the server said".
  const [draft, setDraft] = useState(null)
  const [saved, setSaved] = useState(false)

  const { data, busy, error, mutate } = useAdminResource({
    load: useCallback(() => apiFetch('/api/admin/retention-window'), []),
  })

  // Derived, not synced. Mirroring the server's payload into state through an
  // effect means a render with the old form, a setState, and a second render --
  // and it is what `react-hooks/set-state-in-effect` is warning about. The
  // draft simply wins while it exists, and clearing it after a save re-derives
  // from what the server actually stored, which is the reconcile the save
  // wanted anyway.
  //
  // `enforced !== false` because a row predating the column, or one PostgREST
  // returns without it, must read as enforced rather than as the gate being off.
  const form = draft ?? (data && {
    enforced: data.enforced !== false,
    starts_on: data.starts_on || '',
    ends_on: data.ends_on || '',
    timezone: data.timezone || 'UTC',
  })

  // Checked in the form because the backend's answer to an unresolvable zone is
  // to *deny*, not to fall back -- so a typo here stops recording for every
  // student in the deployment, and the only symptom is a status line saying the
  // window could not be read. `form` is null until the first load, so this is
  // computed after the guards below rather than here.
  const save = async () => {
    setSaved(false)
    const ok = await mutate(() =>
      apiFetch('/api/admin/retention-window', { method: 'PUT', body: form }))
    // Drop the draft so the form re-derives from what came back: the endpoint
    // clamps, so the stored row is the authority, not what was typed.
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
            onChange={e => setDraft({ ...form, enforced: e.target.checked })}
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
            <label className="block text-xs font-bold text-gray-600 dark:text-gray-400 mb-1">Starts on</label>
            <input
              type="date"
              value={form.starts_on}
              disabled={!form.enforced}
              onChange={e => setDraft({ ...form, starts_on: e.target.value })}
              className="w-full rounded-lg border border-gray-300 dark:border-gray-700 bg-white dark:bg-gray-900 px-3 py-2 text-sm disabled:opacity-40"
            />
          </div>
          <div>
            <label className="block text-xs font-bold text-gray-600 dark:text-gray-400 mb-1">Ends on</label>
            <input
              type="date"
              value={form.ends_on}
              disabled={!form.enforced}
              onChange={e => setDraft({ ...form, ends_on: e.target.value })}
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
            onChange={e => setDraft({ ...form, timezone: e.target.value })}
            placeholder="America/Chicago"
            aria-invalid={!tzValid}
            aria-describedby="school-timezone-hint"
            className={`w-full rounded-lg border bg-white dark:bg-gray-900 px-3 py-2 text-sm ${
              tzValid
                ? 'border-gray-300 dark:border-gray-700'
                : 'border-rose-400 dark:border-rose-600'
            }`}
          />
          {/* Suggestions, not the check. An engine without
              `Intl.supportedValuesOf` gets an empty list and a field that still
              validates, rather than a crash. */}
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
