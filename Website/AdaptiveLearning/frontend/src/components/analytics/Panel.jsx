import { motion } from 'framer-motion'
import LoadError from '../ui/LoadError'

/**
 * The card the analytics panels share, and the state ladder they share with it.
 *
 * Four states in a fixed order, because collapsing any two of them is the
 * failure this whole section is built to avoid:
 *
 *   1. `loading`  — a skeleton. Not an empty chart, which reads as "nothing
 *                   recorded" for as long as the request takes.
 *   2. `failed`   — the read did not succeed. A `LoadError` rather than a
 *                   toast: a toast vanishes while the empty state it was
 *                   meant to override stays on the screen.
 *   3. `empty`    — the read worked and found nothing. A sentence saying so.
 *   4. the chart.
 *
 * `failed` comes from the payload's `retrieved` flag, not from a thrown
 * request — the backend answers 200 with a default payload when an aggregate
 * fails, so a caller that only caught rejections would render a quiet week.
 * The page passes both: a rejected fetch sets `retrieved: false` on the way in.
 */
export default function Panel({
  title, note, loading, failed, what, onRetry, empty, emptyNote,
  delay = 0, className = '', children,
}) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }}
      transition={{ delay }}
      className={`bg-white dark:bg-gray-900 rounded-2xl border border-gray-100 dark:border-gray-800 p-5 shadow-sm ${className}`}
    >
      <h3 className="font-black text-gray-900 dark:text-white">{title}</h3>
      {note && <p className="mt-0.5 mb-4 text-xs text-gray-600 dark:text-gray-400">{note}</p>}

      {loading ? (
        <div role="status" aria-label="Loading"
          className="h-56 rounded-2xl bg-gray-100 dark:bg-gray-800 animate-pulse" />
      ) : failed ? (
        <LoadError what={what} onRetry={onRetry} />
      ) : empty ? (
        <p className="py-12 text-center text-sm text-gray-600 dark:text-gray-400">
          {emptyNote}
        </p>
      ) : children}
    </motion.div>
  )
}
