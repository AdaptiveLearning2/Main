/**
 * Tells a student that a parent switched a sensor back on.
 *
 * Only a parent may restore a withdrawn channel, so recording can resume
 * without the student doing anything -- discovering that from data reappearing
 * would be a surprise, not consent. The backend raises `needs_student_ack`
 * when a parent re-enables; this clears it.
 *
 * A parent turning something *off* raises nothing, since the student loses
 * nothing they had.
 *
 * On the dashboard, not mid-question: worth telling someone about, not worth
 * interrupting a maths question for.
 */

import { useState, useEffect } from 'react'
import { ShieldCheck } from 'lucide-react'
import { apiFetch } from '../../lib/api'
import NoticeBanner from './NoticeBanner'

export default function ParentRestoredBanner({ studentId }) {
  const [show, setShow] = useState(false)

  useEffect(() => {
    if (!studentId) return
    let cancelled = false
    apiFetch(`/api/consent/${studentId}`)
      // Only on a genuine `true` -- a failed read answers with defaults, and
      // must not tell a student something happened that may not have.
      .then(c => { if (!cancelled) setShow(c?.needs_student_ack === true) })
      .catch(() => { /* advisory, not a blocker */ })
    return () => { cancelled = true }
  }, [studentId])

  if (!show) return null

  const acknowledge = async () => {
    await apiFetch('/api/consent/ack', { method: 'POST' })
    setShow(false)
  }

  return (
    <NoticeBanner
      tone="indigo" icon={ShieldCheck} onAcknowledge={acknowledge}
      title="A parent turned a sensor back on"
    >
      <p className="text-xs mt-1">
        Something you turned off is being measured again while you practise.
        You can see which, and turn it off again, in your profile settings.
      </p>
    </NoticeBanner>
  )
}
