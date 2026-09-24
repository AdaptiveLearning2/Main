import { useCallback, useRef } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { X } from 'lucide-react'
import useDialog from '../../hooks/useDialog'

/**
 * The slide-in navigation drawer shared by the layouts: a modal dialog with
 * Escape, a focus trap and focus return via `useDialog`.
 */
export default function MobileDrawer({ open, onClose, label = 'Navigation', children }) {
  const panel = useRef(null)

  // Stable, or `useDialog` rebuilds the trap and steals focus each render.
  const close = useCallback(() => onClose?.(), [onClose])

  useDialog(panel, close, open)

  return (
    <AnimatePresence>
      {open && (
        <>
          <motion.div
            initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}
            className="fixed inset-0 bg-black/50 z-40 md:hidden"
            onClick={close}
          />
          <motion.aside
            ref={panel}
            // Can hold focus if nothing inside is focusable.
            tabIndex={-1}
            role="dialog"
            aria-modal="true"
            aria-label={label}
            initial={{ x: '-100%' }} animate={{ x: 0 }} exit={{ x: '-100%' }}
            transition={{ type: 'spring', damping: 25, stiffness: 200 }}
            className="fixed left-0 top-0 bottom-0 w-72 bg-white dark:bg-gray-900 z-50 md:hidden shadow-2xl overflow-y-auto outline-none"
          >
            <button
              onClick={close}
              aria-label="Close menu"
              className="absolute top-4 right-4 p-2 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-800"
            >
              <X size={18} className="text-gray-500 dark:text-gray-400" />
            </button>
            {children}
          </motion.aside>
        </>
      )}
    </AnimatePresence>
  )
}
