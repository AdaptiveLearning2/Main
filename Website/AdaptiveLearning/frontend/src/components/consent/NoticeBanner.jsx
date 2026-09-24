/**
 * The shell the three consent notices share.
 * A failed acknowledgement leaves the banner standing: the person has not been told yet.
 * `onAcknowledge` clears what made the banner render; this owns `busy` (cleared in `finally`).
 */

import { useState } from 'react'
import { motion } from 'framer-motion'

/** Full class strings per tone, never interpolated (Tailwind only ships complete names). */
const TONES = {
  amber: {
    box:   'border-amber-200 dark:border-amber-800 bg-amber-50 dark:bg-amber-950/40',
    icon:  'text-amber-600 dark:text-amber-300',
    title: 'text-amber-900 dark:text-amber-100',
    body:  'text-amber-800 dark:text-amber-200',
    button: 'bg-amber-600 hover:bg-amber-700',
  },
  indigo: {
    box:   'border-indigo-200 dark:border-indigo-800 bg-indigo-50 dark:bg-indigo-950/40',
    icon:  'text-indigo-600 dark:text-indigo-300',
    title: 'text-indigo-900 dark:text-indigo-100',
    body:  'text-indigo-800 dark:text-indigo-200',
    button: 'bg-indigo-600 hover:bg-indigo-700',
  },
  emerald: {
    box:   'border-emerald-200 dark:border-emerald-800 bg-emerald-50 dark:bg-emerald-950/40',
    icon:  'text-emerald-600 dark:text-emerald-300',
    title: 'text-emerald-900 dark:text-emerald-100',
    body:  'text-emerald-800 dark:text-emerald-200',
    button: 'bg-emerald-600 hover:bg-emerald-700',
  },
}

export default function NoticeBanner({
  tone, icon: Icon, title, onAcknowledge, actionLabel = 'Got it', children,
}) {
  const [busy, setBusy] = useState(false)
  const t = TONES[tone]

  const acknowledge = async () => {
    setBusy(true)
    try {
      await onAcknowledge()
    } catch {
      // Swallowed; the banner stays up.
    } finally {
      setBusy(false)
    }
  }

  return (
    <motion.div initial={{ opacity: 0, y: -6 }} animate={{ opacity: 1, y: 0 }}
                className={`mb-4 p-4 rounded-2xl border ${t.box}`}>
      <div className="flex items-start gap-3">
        <Icon className={`${t.icon} flex-shrink-0`} size={18} />
        <div className="min-w-0">
          <p className={`text-sm font-bold ${t.title}`}>{title}</p>
          <div className={t.body}>{children}</div>
          <button onClick={acknowledge} disabled={busy}
                  className={`mt-3 px-3 py-1.5 rounded-lg ${t.button} text-white text-xs font-bold disabled:opacity-50`}>
            {busy ? 'Saving…' : actionLabel}
          </button>
        </div>
      </div>
    </motion.div>
  )
}
