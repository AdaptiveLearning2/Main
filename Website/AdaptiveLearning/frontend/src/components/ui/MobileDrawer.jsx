import { useCallback, useRef } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { X } from 'lucide-react'
import useDialog from '../../hooks/useDialog'

/**
 * The slide-in navigation drawer, once.
 *
 * All four layouts carried a byte-identical copy of this — overlay, panel,
 * spring, close button — and all four were missing the same three things, so
 * the fix would otherwise have been the same edit made four times and then
 * drifted apart. That is the shape `_close_session` exists to prevent on the
 * backend.
 *
 * What they were missing, via `useDialog`:
 *
 * - **Escape closed nothing.** The backdrop was the only way out, so a
 *   keyboard user who opened the menu had none — on the control that is the
 *   whole of the navigation on a phone.
 * - **Tab walked out of it.** Straight into the page behind, which is still
 *   there and still focusable under an opaque overlay, with no visible cursor
 *   to say focus had left.
 * - **Focus did not come back.** Closing dropped it to the top of the
 *   document, so the reader lost their place every time they opened the menu
 *   and changed their mind.
 *
 * `role="dialog"` + `aria-modal` is the other half: without it a screen reader
 * announces the page behind as though it were still available, which is
 * exactly the confusion the trap prevents for a sighted keyboard user.
 *
 * Deliberately not a general `<Modal>`. `Questions.jsx`'s overlay has its own
 * markup and its own animation and shares only the behaviour, which is why
 * `useDialog` is a hook. This is the one shape that genuinely repeats.
 */
export default function MobileDrawer({ open, onClose, label = 'Navigation', children }) {
  const panel = useRef(null)

  // Memoised because `useDialog` lists it in its dependencies: a fresh closure
  // every render would tear down and rebuild the trap on each one, re-focusing
  // the first item and stealing focus from wherever the user had tabbed to.
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
            // `tabIndex={-1}` so the panel itself can hold focus if it ever
            // renders with nothing focusable inside. Without somewhere to put
            // it, focus stays on the page behind and there is nothing for the
            // trap to trap.
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
              <X size={18} className="text-gray-500" />
            </button>
            {children}
          </motion.aside>
        </>
      )}
    </AnimatePresence>
  )
}
