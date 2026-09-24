import { motion } from 'framer-motion'
import LoadError from '../ui/LoadError'

/**
 * The analytics panel card and its state ladder: loading, failed, empty, content.
 * `failed` comes from the payload's `retrieved` flag (a failed aggregate still answers 200).
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
