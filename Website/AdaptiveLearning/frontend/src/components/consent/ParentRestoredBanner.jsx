/**
 * Tells a student that a parent switched a sensor on (clears
 * `needs_student_ack`). Dashboard only, never mid-question.
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
      // Only on a genuine `true`; a failed read answers with defaults.
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
      title="A parent turned a sensor on"
    >
      <p className="text-xs mt-1">
        A sensor is now measured while you practise. It may be one you turned off
        before. You can see which, and turn it off, in your profile settings.
      </p>
    </NoticeBanner>
  )
}
