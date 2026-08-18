/**
 * Tells a parent that a child has switched a sensor off.
 *
 * The consent model notified in one direction only. A parent re-enabling a
 * channel raises `needs_student_ack` and `ParentRestoredBanner` clears it;
 * nothing told a *parent* when their child turned one off. They found out by
 * opening Settings and reading "Off since <date>" on a panel they had no reason
 * to visit — so the one event that changes what is being measured was the event
 * nobody was told about.
 *
 * **This is a notice, not a prompt to undo it.** A student's withdrawal stands,
 * and a parent can already restore a channel from Settings if that is the right
 * conversation to have. Putting a "turn it back on" button here would make
 * overriding a child's decision the one-click default response to being told
 * they made it, which is the opposite of what the asymmetry is for. It links to
 * Settings and says nothing about what they should do there.
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
      // Only on a genuine retrieved read. The endpoint fails open to an empty
      // list, and drawing this off a failure would tell a parent their child
      // withdrew something they may not have.
      .then(r => { if (!cancelled && r?.retrieved) setNotices(r.notices || []) })
      .catch(() => { /* silent: advisory, not a blocker */ })
    return () => { cancelled = true }
  }, [])

  if (notices.length === 0) return null

  const acknowledge = async () => {
    setBusy(true)
    try {
      await apiFetch('/api/parent/consent-notices/ack', { method: 'POST' })
      setNotices([])
    } catch {
      // Left up. As far as the record is concerned the parent has not been
      // told, and hiding it here would lose that.
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
