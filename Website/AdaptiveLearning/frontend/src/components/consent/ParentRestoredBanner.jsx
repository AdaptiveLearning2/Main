/**
 * Tells a student that a parent switched a sensor back on.
 *
 * A student may withdraw at any time and only a parent may restore. That
 * asymmetry is the right one, but it means a channel can start recording again
 * without the student doing anything — and discovering that by noticing data
 * reappear is a surprise, not consent. The backend raises `needs_student_ack`
 * when a parent re-enables; this is the surface that clears it.
 *
 * A parent turning something *off* raises nothing. The student loses nothing
 * they had, and can see the state in settings.
 *
 * On the dashboard, deliberately not mid-question. A sensor resuming is worth
 * telling someone about; interrupting a maths question to do it is not, and the
 * plan rules out a first-run explainer or an acknowledgement gate for the same
 * reason.
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
      // Only on a genuine `true`. A failed read answers with defaults, and
      // showing this off the back of one would tell a student something
      // happened that may not have.
      .then(c => { if (!cancelled) setShow(c?.needs_student_ack === true) })
      .catch(() => { /* silent: this is an advisory, not a blocker */ })
    return () => { cancelled = true }
  }, [studentId])

  if (!show) return null

  const acknowledge = async () => {
    setBusy(true)
    try {
      await apiFetch('/api/consent/ack', { method: 'POST' })
      setShow(false)
    } catch {
      // Left up. If the acknowledgement did not land, the student has not been
      // told as far as the record is concerned, and hiding it would lose that.
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
