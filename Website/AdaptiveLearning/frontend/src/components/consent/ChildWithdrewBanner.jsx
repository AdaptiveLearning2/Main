/**
 * Tells a parent that a child switched a sensor off, or another parent account changed something.
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

  const withdrawn = notices.some(n => n.channels?.length)
  const changed = notices.some(n => n.parent_changes?.length)

  return (
    <NoticeBanner
      tone="amber" icon={BellRing} onAcknowledge={acknowledge}
      title={changed
        ? 'Another parent account made changes'
        : notices.length > 1
          ? 'Some sensors were switched off'
          : `${notices[0].child_name} switched a sensor off`}
    >
      <ul className="mt-1 space-y-0.5">
        {notices.map(n => (n.channels || []).map(c => (
          <li key={`${n.child_id}-${c.channel}`} className="text-xs">
            {n.child_name} turned off {c.label}
            {fmtDate(c.at) && <> on {fmtDate(c.at)}</>}.
          </li>
        )))}
        {notices.map(n => (n.parent_changes || []).map((c, i) => (
          <li key={`${n.child_id}-change-${i}`} className="text-xs">
            {changeText(c, n.child_name)}
            {fmtDate(c.at) && <> on {fmtDate(c.at)}</>}.
          </li>
        )))}
      </ul>
      {withdrawn && (
        <p className="text-xs mt-2">
          Nothing from that sensor is measured or saved while it is off. What
          was recorded before is unchanged. You can see the full picture in{' '}
          <Link to="/parent/settings" className="underline font-bold">Settings</Link>.
        </p>
      )}
      {changed && (
        <p className="text-xs mt-2">
          Any linked parent account can do this. If you did not expect it, check{' '}
          <Link to="/parent/settings" className="underline font-bold">Settings</Link>{' '}
          and contact your child&apos;s school.
        </p>
      )}
    </NoticeBanner>
  )
}

/** Names no account: the child may have created it, so its name would be their word. */
function changeText(c, child) {
  if (c.kind === 'parent_linked') return `Another parent account was linked to ${child}`
  if (c.kind === 'channel_enabled') return `Another parent account turned on ${c.label} for ${child}`
  if (c.kind === 'channel_erased') return `Another parent account erased what ${c.label} recorded for ${child}`
  // A kind this page does not know yet is still shown, never dropped.
  return `Another parent account changed ${child}'s settings`
}
