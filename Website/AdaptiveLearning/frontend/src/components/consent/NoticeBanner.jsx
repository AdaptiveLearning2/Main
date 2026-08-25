/**
 * The shell the three consent notices share.
 *
 * `ChildWithdrewBanner`, `ParentRestoredBanner` and `ParentLinkedBanner` are
 * one component wearing three colours: the same motion wrapper, the same icon
 * and title row, and the same "Got it" button. What differs is the tone, the
 * icon, the words, and which endpoint the acknowledgement goes to.
 *
 * **The acknowledgement behaviour is the reason this is a component and not a
 * copied `<div>`.** All three have to leave the banner *up* when the ack fails,
 * because the person has not actually been told yet — a notice that dismisses
 * itself on a failed write is a notice nobody ever sees again. That rule was
 * stated identically in three files, which is two more places for it to be
 * dropped by whoever adds the fourth notice.
 *
 * `onAcknowledge` owns clearing whatever made the banner render; this owns the
 * pending flag and swallowing the failure. `busy` is cleared in a `finally`
 * rather than only on the failure path, so a caller that acknowledges without
 * unmounting does not leave a permanently disabled button.
 */

import { useState } from 'react'
import { motion } from 'framer-motion'

/** Full class strings per tone, never interpolated.
 *
 * Tailwind scans source text for complete class names, so `border-${tone}-200`
 * produces markup referring to CSS that was never generated — the banner would
 * render with no border and no background at all, and only in a production
 * build, where the scan is what decides which utilities ship.
 */
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
      // Swallowed, and the banner is left standing: the person has not been
      // told yet, so dismissing here would lose the notice for good.
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
          {/* The body colour lives here rather than on each child, so a notice
              never has to name its own tone twice. */}
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
