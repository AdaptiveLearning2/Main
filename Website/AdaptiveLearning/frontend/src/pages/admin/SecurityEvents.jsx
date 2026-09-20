import { useCallback, useState } from 'react'
import { ShieldAlert, Gauge, KeyRound, UserCheck } from 'lucide-react'
import { apiFetch } from '../../lib/api'
import useAdminResource from '../../hooks/useAdminResource'
import LoadError from '../../components/ui/LoadError'

// One entry per kind the backend writes. The sentence is what the row means,
// not what the column says: `authz_denied` is accurate and tells an admin
// nothing they can act on.
//
// Keyed by the backend's own vocabulary, and an unrecognised kind still
// renders -- see `UNKNOWN` below. The CHECK constraint makes one near
// impossible, and if it happens a visible row is what gets it reported, which
// is the rule `AlertFeed` already holds for session alerts.
const KINDS = {
  authz_denied: {
    label: 'Access refused',
    icon: ShieldAlert,
    tone: 'text-amber-700 dark:text-amber-300',
    describe: (e, subject) => e.subject
      ? `tried to read ${subject}'s data`
      : 'was refused access',
  },
  admin_denied: {
    label: 'Admin refused',
    icon: KeyRound,
    tone: 'text-rose-700 dark:text-rose-300',
    describe: (e) => `tried to open ${e.detail?.path || 'the admin console'}`,
  },
  rate_limited: {
    label: 'Rate limited',
    icon: Gauge,
    tone: 'text-indigo-700 dark:text-indigo-300',
    describe: (e) => `sent too many ${e.detail?.limiter || 'requests'} requests`,
  },
  consent_changed: {
    label: 'Consent changed',
    icon: UserCheck,
    tone: 'text-emerald-700 dark:text-emerald-300',
    describe: (e, subject) => {
      const parts = []
      if (e.detail?.withdrew) parts.push(`turned off ${e.detail.withdrew}`)
      if (e.detail?.re_enabled) parts.push('turned a channel back on')
      const what = parts.join(' and ') || 'changed consent'
      return e.subject ? `${what} for ${subject}` : what
    },
  },
}

const UNKNOWN = {
  label: 'Unrecognised',
  icon: ShieldAlert,
  tone: 'text-gray-600 dark:text-gray-400',
  describe: () => 'was recorded by a newer backend than this page knows about',
}

// Three states, because the backend now sends a null name rather than a
// substituted one: a name it read, an account it found no profile for, and a
// name lookup that failed. The last two look identical if collapsed, and on an
// audit page "this account does not exist" is a claim a failed read has not
// earned -- the same rule `SignalPanel`'s `Unavailable` tile holds.
// The id is rendered beside every one of these, so the row is actionable
// whichever state it is in.
function nameOf(who, namesRetrieved, capital) {
  if (who?.name) return who.name
  if (namesRetrieved) return capital ? 'An unnamed account' : 'an unnamed account'
  return capital ? 'An account whose name could not be read'
    : 'an account whose name could not be read'
}

function when(iso) {
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? '—' : d.toLocaleString()
}

export default function AdminSecurityEvents() {
  // `null` is "every kind", which is the default view: an admin opening this
  // page is asking "what happened", not "what happened of one type".
  const [kind, setKind] = useState(null)

  // Memoised on `kind`, or `useAdminResource`'s effect re-runs every render.
  const load = useCallback(
    () => apiFetch(`/api/admin/security-events${kind ? `?kind=${kind}` : ''}`),
    [kind])

  const { data, error } = useAdminResource({ load })

  if (error) return <LoadError error={error} />
  if (!data) return <p className="text-gray-600 dark:text-gray-400">Loading…</p>

  // Three states, not two. A failed read must never render as "nothing has
  // happened" -- on this page that is the worst available wrong answer, since
  // an empty log is exactly what someone covering their tracks would want it
  // to look like.
  // Not `LoadError`: that one is for a request that failed, and picks its
  // sentence from `error.status` -- it ignores a message a caller passes, so
  // wording this through it silently rendered the generic "make sure the
  // backend is running" instead. This is the other state: the request
  // succeeded and the read inside it did not.
  if (!data.retrieved) {
    return (
      <div className="rounded-xl border border-amber-200 bg-amber-50 p-4
                      dark:border-amber-900 dark:bg-amber-950/40">
        <p className="font-bold text-amber-900 dark:text-amber-200">
          The security log could not be read.
        </p>
        <p className="mt-1 text-sm text-amber-800 dark:text-amber-300">
          This is not the same as it being empty — no conclusion should be drawn
          about what has or has not happened.
        </p>
      </div>
    )
  }

  const events = data.events || []

  return (
    <div className="space-y-5">
      <header>
        <h1 className="text-xl font-black text-gray-900 dark:text-white">Security log</h1>
        <p className="mt-1 text-sm text-gray-600 dark:text-gray-400">
          Refused access, rate limits and consent changes. Kept for 180 days.
          Never contains sensor readings.
        </p>
      </header>

      <div className="flex flex-wrap gap-2">
        {/* The same selected/unselected pair `SeriesFilter` uses, and for the
            reason its own comment gives: an inverted chip (`dark:bg-white`
            with `dark:text-gray-900`) is correct on screen but fails the
            contrast pairing check, which resolves a grey against the dark
            surfaces the page paints rather than against a background set in
            the same class string. Naming a grey on both sides keeps the check
            able to do its arithmetic. */}
        <button
          onClick={() => setKind(null)}
          aria-pressed={kind === null}
          className={`px-3 py-2.5 min-h-[44px] rounded-lg border text-xs font-bold ${
            kind === null
              ? 'border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-900 text-gray-900 dark:text-gray-100'
              : 'border-gray-200 dark:border-gray-700 bg-slate-50 dark:bg-gray-800 text-gray-600 dark:text-gray-400'}`}
        >
          All
        </button>
        {(data.kinds || []).map(k => (
          <button
            key={k}
            onClick={() => setKind(k)}
            aria-pressed={kind === k}
            className={`px-3 py-2.5 min-h-[44px] rounded-lg border text-xs font-bold ${
              kind === k
                ? 'border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-900 text-gray-900 dark:text-gray-100'
                : 'border-gray-200 dark:border-gray-700 bg-slate-50 dark:bg-gray-800 text-gray-600 dark:text-gray-400'}`}
          >
            {(KINDS[k] || UNKNOWN).label}
          </button>
        ))}
      </div>

      {events.length === 0 ? (
        // Distinct from the unreadable case above, and worded so the two can
        // never be confused by a reader skimming.
        <p className="text-sm text-gray-600 dark:text-gray-400">
          The log was read and holds no {kind ? 'events of this kind' : 'events'}.
        </p>
      ) : (
        <ul className="divide-y divide-gray-100 dark:divide-gray-800">
          {events.map(e => {
            const spec = KINDS[e.kind] || UNKNOWN
            const Icon = spec.icon
            return (
              <li key={e.id} className="py-3 flex gap-3">
                <Icon size={16} className={`mt-0.5 shrink-0 ${spec.tone}`} aria-hidden="true" />
                <div className="min-w-0">
                  <p className="text-sm text-gray-900 dark:text-white">
                    <span className="font-bold">{spec.label}</span>
                    {' — '}
                    {/* The actor's name for reading and the id for looking the
                        account up. A uuid alone is true and unusable; a name
                        alone cannot be acted on. */}
                    <span>{nameOf(e.actor, data.names_retrieved, true)}</span>
                    {' '}
                    {spec.describe(e, nameOf(e.subject, data.names_retrieved, false))}
                  </p>
                  <p className="text-[11px] text-gray-600 dark:text-gray-400">
                    {when(e.created_at)}
                    {e.actor?.id && <> · <code>{e.actor.id}</code></>}
                  </p>
                </div>
              </li>
            )
          })}
        </ul>
      )}
    </div>
  )
}
