import { useCallback, useState } from 'react'

/** Open/closed state for a `MobileDrawer`, with stable handlers.
 *
 * Extracting `MobileDrawer` left the four layouts each holding an identical
 * copy of the wiring around it — the `useState`, the memoised closer, the
 * opener — which is the same drift the component extraction was for, one level
 * out. Four copies of three lines is still four places for the next change to
 * land in three of.
 *
 * The memoisation is the part that is easy to drop and quietly costs something:
 * `MobileDrawer` hands `onClose` to `useDialog`, which lists it in its
 * dependencies, so a fresh closure per render tears down and rebuilds the focus
 * trap on every one — pulling focus back to the first item while the user is
 * tabbing. A hook makes that correct once rather than correct four times.
 */
export default function useMobileDrawer() {
  const [open, setOpen] = useState(false)
  const onOpen  = useCallback(() => setOpen(true), [])
  const onClose = useCallback(() => setOpen(false), [])
  return { open, onOpen, onClose }
}
