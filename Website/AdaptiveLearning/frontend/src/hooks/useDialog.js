import { useEffect } from 'react'

// Everything focusable, minus anything explicitly taken out of the tab order.
const FOCUSABLE = [
  'a[href]', 'button:not([disabled])', 'input:not([disabled])',
  'select:not([disabled])', 'textarea:not([disabled])', '[tabindex]:not([tabindex="-1"])',
].join(',')

/** Make an overlay behave like a dialog for someone using a keyboard.
 *
 * Three things a plain `<div>` with a backdrop click doesn't do on its own:
 * Escape closes it, Tab stays inside it, and closing returns focus to
 * whatever opened it instead of resetting to the top of the document.
 *
 * A hook rather than a `<Modal>` component, since the overlays here already
 * have their own markup and animation and only needed the behavior.
 *
 * @param ref      the element that holds the dialog's focusable content
 * @param onClose  called on Escape; should be stable or memoised
 * @param active   whether the dialog is open. Nothing is bound when false.
 */
export default function useDialog(ref, onClose, active = true) {
  useEffect(() => {
    if (!active) return undefined
    const node = ref.current
    if (!node) return undefined

    // Captured before we move focus, so it is the thing that opened us.
    const restoreTo = document.activeElement

    // No visibility filter (e.g. `offsetParent`) — that's always null under
    // jsdom, which has no layout, so it would break every test while
    // looking fine in a browser. The selector's own disabled/tabindex
    // exclusions are enough.
    const focusables = () => Array.from(node.querySelectorAll(FOCUSABLE))

    // Focus the first focusable thing, or the dialog itself, so focus
    // actually moves into the overlay and the trap below has something to hold.
    const first = focusables()[0]
    if (first) first.focus()
    else if (node.tabIndex >= 0) node.focus()

    const onKeyDown = (e) => {
      if (e.key === 'Escape') {
        e.stopPropagation()
        onClose?.()
        return
      }
      if (e.key !== 'Tab') return

      const items = focusables()
      if (items.length === 0) {
        e.preventDefault()
        return
      }
      const firstItem = items[0]
      const lastItem = items[items.length - 1]
      // `document.activeElement`, not `e.target`, since focus can be on
      // the container itself.
      if (e.shiftKey && document.activeElement === firstItem) {
        e.preventDefault()
        lastItem.focus()
      } else if (!e.shiftKey && document.activeElement === lastItem) {
        e.preventDefault()
        firstItem.focus()
      }
    }

    document.addEventListener('keydown', onKeyDown)
    return () => {
      document.removeEventListener('keydown', onKeyDown)
      // Only if it's still in the document — the opener may have been
      // unmounted by whatever the dialog did.
      if (restoreTo && document.contains(restoreTo)) restoreTo.focus?.()
    }
  }, [ref, onClose, active])
}
