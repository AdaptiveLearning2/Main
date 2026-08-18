/**
 * Tells a student that a parent has linked to their account.
 *
 * `POST /api/parent/link-child` needs nothing from the child — a parent who
 * knows their user id creates the link — and from that moment
 * `_verify_can_view_student` reads it as entitlement to their reports, and a
 * sensor the student switched off can be switched back on over their head.
 * Nothing told them it had happened.
 *
 * **Notify, not block**, which is a decision rather than the easier option. An
 * acknowledgement gate would put a child between a parent and reports the
 * parent is entitled to, and this product's students are children — some of
 * whom would never clear it. So this says what happened, is dismissible, and
 * nothing anywhere waits on it.
 *
 * On the dashboard, deliberately not mid-question, for the same reason
 * `ParentRestoredBanner` sits there: worth telling someone about, not worth
 * interrupting a maths question for.
 */

import { useEffect, useState } from 'react'
import { motion } from 'framer-motion'
import { UserPlus } from 'lucide-react'
import { apiFetch } from '../../lib/api'

function fmtDate(s) {
  if (!s) return null
  const d = new Date(s)
  return Number.isNaN(d.getTime())
    ? null
    : d.toLocaleDateString([], { month: 'long', day: 'numeric' })
}

export default function ParentLinkedBanner({ studentId }) {
  const [links, setLinks] = useState([])
  const [busy, setBusy]   = useState(false)

  useEffect(() => {
    if (!studentId) return undefined
    let cancelled = false
    apiFetch('/api/student/parent-links')
      // Only on a genuine retrieved read. The endpoint fails open to an empty
      // list, and drawing this off the back of a failure would tell a child
      // something happened to their account that may not have.
      .then(r => { if (!cancelled && r?.retrieved) setLinks(r.links || []) })
      .catch(() => { /* silent: this is an advisory, not a blocker */ })
    return () => { cancelled = true }
  }, [studentId])

  if (links.length === 0) return null

  const acknowledge = async () => {
    setBusy(true)
    try {
      await apiFetch('/api/student/parent-links/ack', { method: 'POST' })
      setLinks([])
    } catch {
      // Left up. If the acknowledgement did not land, the student has not been
      // told as far as the record is concerned, and hiding it would lose that.
      setBusy(false)
    }
  }

  const names = links.map(l => l.parent_name).join(', ')
  const when  = fmtDate(links[0]?.linked_at)

  return (
    <motion.div initial={{ opacity: 0, y: -6 }} animate={{ opacity: 1, y: 0 }}
                className="mb-4 p-4 rounded-2xl border border-emerald-200 dark:border-emerald-800 bg-emerald-50 dark:bg-emerald-950/40">
      <div className="flex items-start gap-3">
        <UserPlus className="text-emerald-600 dark:text-emerald-300 flex-shrink-0" size={18} />
        <div className="min-w-0">
          <p className="text-sm font-bold text-emerald-900 dark:text-emerald-100">
            {links.length > 1
              ? `${links.length} parents linked to your account`
              : `${names} linked to your account`}
            {when && <span className="font-normal"> on {when}</span>}
          </p>
          {/* What the link actually grants, in the two sentences that matter to
              a student: they can see your work, and they can turn a sensor back
              on that you turned off. Both are true the moment the link exists,
              so saying only "a parent is now linked" would understate it. */}
          <p className="text-xs text-emerald-800 dark:text-emerald-200 mt-1">
            They can see your progress reports, and can turn a sensor back on
            if you have turned one off. You will always be told when that
            happens.
          </p>
          <button onClick={acknowledge} disabled={busy}
                  className="mt-3 px-3 py-1.5 rounded-lg bg-emerald-600 hover:bg-emerald-700 text-white text-xs font-bold disabled:opacity-50">
            {busy ? 'Saving…' : 'Got it'}
          </button>
        </div>
      </div>
    </motion.div>
  )
}
