import { useCallback, useState } from 'react'

/**
 * Open/closed state for a `MobileDrawer`. Handlers are stable: `useDialog`
 * depends on `onClose`, and a new closure would rebuild the focus trap.
 */
export default function useMobileDrawer() {
  const [open, setOpen] = useState(false)
  const onOpen  = useCallback(() => setOpen(true), [])
  const onClose = useCallback(() => setOpen(false), [])
  return { open, onOpen, onClose }
}
