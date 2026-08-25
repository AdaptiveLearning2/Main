/**
 * Tells a student that a parent has linked to their account.
 *
 * A parent can link to a child's account knowing only their user id, and from
 * that moment can read their reports and switch a sensor back on. Nothing
 * told the student until this existed.
 *
 * **Notify, not block.** An acknowledgement gate would put a child between a
 * parent and reports the parent is entitled to, and some children would
 * never clear it. So this is dismissible and nothing waits on it.
 *
 * On the dashboard, not mid-question, same as `ParentRestoredBanner`: worth
 * telling someone about, not worth interrupting a maths question for.
 */

import { useEffect, useState } from 'react'
import { UserPlus } from 'lucide-react'
import { apiFetch } from '../../lib/api'
import { fmtDate } from '../../lib/dates'
import NoticeBanner from './NoticeBanner'

export default function ParentLinkedBanner({ studentId }) {
  const [links, setLinks] = useState([])

  useEffect(() => {
    if (!studentId) return undefined
    let cancelled = false
    apiFetch('/api/student/parent-links')
      // Only on a genuine retrieved read -- a failed read must not tell a
      // child something happened to their account that may not have.
      .then(r => { if (!cancelled && r?.retrieved) setLinks(r.links || []) })
      .catch(() => { /* advisory, not a blocker */ })
    return () => { cancelled = true }
  }, [studentId])

  if (links.length === 0) return null

  const acknowledge = async () => {
    await apiFetch('/api/student/parent-links/ack', { method: 'POST' })
    setLinks([])
  }

  const names = links.map(l => l.parent_name).join(', ')
  const when  = fmtDate(links[0]?.linked_at)

  return (
    <NoticeBanner
      tone="emerald" icon={UserPlus} onAcknowledge={acknowledge}
      title={<>
        {links.length > 1
          ? `${links.length} parents linked to your account`
          : `${names} linked to your account`}
        {when && <span className="font-normal"> on {when}</span>}
      </>}
    >
      {/* Both powers are real the moment the link exists, so naming only
          "a parent is now linked" would understate it. */}
      <p className="text-xs mt-1">
        They can see your progress reports, and can turn a sensor back on
        if you have turned one off. You will always be told when that
        happens.
      </p>
    </NoticeBanner>
  )
}
