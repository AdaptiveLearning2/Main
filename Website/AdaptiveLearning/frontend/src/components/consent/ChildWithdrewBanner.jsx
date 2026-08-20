/**
 * Tells a parent that a child has switched a sensor off.
 *
 * Notification used to run only one way: a parent re-enabling a channel
 * notified the student, but a child withdrawing one told nobody.
 *
 * **This is a notice, not a prompt to undo it.** A student's withdrawal
 * stands; a parent can restore a channel from Settings if that's the right
 * call. A "turn it back on" button here would make overriding the child's
 * decision the default response to hearing about it.
 */

import { useEffect, useState } from 'react'
import { motion } from 'framer-motion'
import { BellRing } from 'lucide-react'
import { Link } from 'react-router-dom'
import { apiFetch } from '../../lib/api'

function fmtDate(s) {
  if (!s) return null
  const d = new Date(s)
  return Number.isNaN(d.getTime())
    ? null
    : d.toLocaleDateString([], { month: 'long', day: 'numeric' })
}

export default function ChildWithdrewBanner() {
  const [notices, setNotices] = useState([])
  const [busy, setBusy]       = useState(false)

  useEffect(() => {
    let cancelled = false
    apiFetch('/api/parent/consent-notices')
      // Only on a genuine retrieved read -- a failed read must not be shown as
      // "child withdrew something", since it fails open to an empty list.
      .then(r => { if (!cancelled && r?.retrieved) setNotices(r.notices || []) })
      .catch(() => { /* advisory, not a blocker */ })
    return () => { cancelled = true }
  }, [])

  if (notices.length === 0) return null

  const acknowledge = async () => {
    setBusy(true)
    try {
      // Sends back the server's own watermark per child rather than letting the
      // endpoint stamp `now()` -- otherwise a withdrawal that lands between the
      // read and the dismiss click would be marked seen and never shown.
      const through = Object.fromEntries(
        notices.filter(n => n.through).map(n => [n.child_id, n.through]))
      await apiFetch('/api/parent/consent-notices/ack',
                     { method: 'POST', body: { through } })
      setNotices([])
    } catch {
      // Left up: the parent has not actually been told yet.
      setBusy(false)
    }
  }

  return (
    <motion.div initial={{ opacity: 0, y: -6 }} animate={{ opacity: 1, y: 0 }}
                className="mb-4 p-4 rounded-2xl border border-amber-200 dark:border-amber-800 bg-amber-50 dark:bg-amber-950/40">
      <div className="flex items-start gap-3">
        <BellRing className="text-amber-600 dark:text-amber-300 flex-shrink-0" size={18} />
        <div className="min-w-0">
          <p className="text-sm font-bold text-amber-900 dark:text-amber-100">
            {notices.length > 1
              ? 'Some sensors were switched off'
              : `${notices[0].child_name} switched a sensor off`}
          </p>
          <ul className="mt-1 space-y-0.5">
            {notices.map(n => n.channels.map(c => (
              <li key={`${n.child_id}-${c.channel}`}
                  className="text-xs text-amber-800 dark:text-amber-200">
                {n.child_name} turned off {c.label}
                {fmtDate(c.at) && <> on {fmtDate(c.at)}</>}.
              </li>
            )))}
          </ul>
          <p className="text-xs text-amber-800 dark:text-amber-200 mt-2">
            Nothing from that sensor is measured or saved while it is off. What
            was recorded before is unchanged. You can see the full picture in{' '}
            <Link to="/parent/settings" className="underline font-bold">Settings</Link>.
          </p>
          <button onClick={acknowledge} disabled={busy}
                  className="mt-3 px-3 py-1.5 rounded-lg bg-amber-600 hover:bg-amber-700 text-white text-xs font-bold disabled:opacity-50">
            {busy ? 'Saving…' : 'Got it'}
          </button>
        </div>
      </div>
    </motion.div>
  )
}
