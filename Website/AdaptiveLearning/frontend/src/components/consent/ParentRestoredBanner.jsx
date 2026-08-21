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
import { motion } from 'framer-motion'
import { ShieldCheck } from 'lucide-react'
import { apiFetch } from '../../lib/api'

export default function ParentRestoredBanner({ studentId }) {
  const [show, setShow]   = useState(false)
  const [busy, setBusy]   = useState(false)

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
    setBusy(true)
    try {
      await apiFetch('/api/consent/ack', { method: 'POST' })
      setShow(false)
    } catch {
      // Left up: the student has not actually been told yet.
      setBusy(false)
    }
  }

  return (
    <motion.div initial={{ opacity: 0, y: -6 }} animate={{ opacity: 1, y: 0 }}
                className="mb-4 p-4 rounded-2xl border border-indigo-200 dark:border-indigo-800 bg-indigo-50 dark:bg-indigo-950/40">
      <div className="flex items-start gap-3">
        <ShieldCheck className="text-indigo-600 dark:text-indigo-300 flex-shrink-0" size={18} />
        <div className="min-w-0">
          <p className="text-sm font-bold text-indigo-900 dark:text-indigo-100">
            A parent turned a sensor back on
          </p>
          <p className="text-xs text-indigo-800 dark:text-indigo-200 mt-1">
            Something you turned off is being measured again while you practise.
            You can see which, and turn it off again, in your profile settings.
          </p>
          <button onClick={acknowledge} disabled={busy}
                  className="mt-3 px-3 py-1.5 rounded-lg bg-indigo-600 hover:bg-indigo-700 text-white text-xs font-bold disabled:opacity-50">
            {busy ? 'Saving…' : 'Got it'}
          </button>
        </div>
      </div>
    </motion.div>
  )
}
