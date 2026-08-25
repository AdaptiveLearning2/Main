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
import { BellRing } from 'lucide-react'
import { Link } from 'react-router-dom'
import { apiFetch } from '../../lib/api'
import { fmtDate } from '../../lib/dates'
import NoticeBanner from './NoticeBanner'

export default function ChildWithdrewBanner() {
  const [notices, setNotices] = useState([])

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
    // Sends back the server's own watermark per child rather than letting the
    // endpoint stamp `now()` -- otherwise a withdrawal that lands between the
    // read and the dismiss click would be marked seen and never shown.
    const through = Object.fromEntries(
      notices.filter(n => n.through).map(n => [n.child_id, n.through]))
    await apiFetch('/api/parent/consent-notices/ack',
                   { method: 'POST', body: { through } })
    setNotices([])
  }

  return (
    <NoticeBanner
      tone="amber" icon={BellRing} onAcknowledge={acknowledge}
      title={notices.length > 1
        ? 'Some sensors were switched off'
        : `${notices[0].child_name} switched a sensor off`}
    >
      <ul className="mt-1 space-y-0.5">
        {notices.map(n => n.channels.map(c => (
          <li key={`${n.child_id}-${c.channel}`} className="text-xs">
            {n.child_name} turned off {c.label}
            {fmtDate(c.at) && <> on {fmtDate(c.at)}</>}.
          </li>
        )))}
      </ul>
      <p className="text-xs mt-2">
        Nothing from that sensor is measured or saved while it is off. What
        was recorded before is unchanged. You can see the full picture in{' '}
        <Link to="/parent/settings" className="underline font-bold">Settings</Link>.
      </p>
    </NoticeBanner>
  )
}
