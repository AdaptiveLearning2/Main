import { useCallback, useState } from 'react'

/** Open/closed state for a `MobileDrawer`, with stable handlers.
 *
 * Shared so each layout doesn't repeat the same `useState` + handlers.
 *
 * The memoization matters: `MobileDrawer` passes `onClose` to `useDialog`,
 * which depends on it, so a new closure per render would tear down and
 * rebuild the focus trap on every render — pulling focus away while the
 * user is tabbing.
 */
export default function useMobileDrawer() {
  const [open, setOpen] = useState(false)
  const onOpen  = useCallback(() => setOpen(true), [])
  const onClose = useCallback(() => setOpen(false), [])
  return { open, onOpen, onClose }
}
