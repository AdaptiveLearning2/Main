/**
 * Tells a parent that a child has switched a sensor off.
 * A notice, not a prompt to undo it: deliberately no "turn it back on" button.
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
      // Only on a retrieved read.
      .then(r => { if (!cancelled && r?.retrieved) setNotices(r.notices || []) })
      .catch(() => { /* advisory, not a blocker */ })
    return () => { cancelled = true }
  }, [])

  if (notices.length === 0) return null

  const acknowledge = async () => {
    // The server's watermark per child, not `now()`, so a later withdrawal is never marked seen.
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
