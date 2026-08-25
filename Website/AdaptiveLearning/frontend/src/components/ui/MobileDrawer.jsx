import { useCallback, useRef } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { X } from 'lucide-react'
import useDialog from '../../hooks/useDialog'

/**
 * The slide-in navigation drawer, once.
 *
 * All four layouts carried an identical copy of this and were all missing the
 * same three things, via `useDialog`:
 *
 * - **Escape closed nothing.** The backdrop was the only way out.
 * - **Tab walked out of it**, into the still-focusable page behind, with no
 *   visible cursor to say focus had left.
 * - **Focus did not come back.** Closing dropped it to the top of the
 *   document, losing the reader's place each time.
 *
 * `role="dialog"` + `aria-modal` is the other half: without it a screen
 * reader announces the page behind as though it were still available.
 *
 * Not a general `<Modal>` -- `Questions.jsx`'s overlay has its own markup and
 * animation and shares only the behaviour, which is why `useDialog` is a hook.
 */
export default function MobileDrawer({ open, onClose, label = 'Navigation', children }) {
  const panel = useRef(null)

  // Memoised because `useDialog` depends on it -- a fresh closure every
  // render would rebuild the trap and steal focus from wherever the user
  // had tabbed to.
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
            // `tabIndex={-1}` so the panel can hold focus if it ever renders
            // with nothing focusable inside.
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
