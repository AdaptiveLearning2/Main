import { useEffect } from 'react'

// Everything focusable, minus anything explicitly taken out of the tab order.
const FOCUSABLE = [
  'a[href]', 'button:not([disabled])', 'input:not([disabled])',
  'select:not([disabled])', 'textarea:not([disabled])', '[tabindex]:not([tabindex="-1"])',
].join(',')

/** Make an overlay behave like a dialog for someone using a keyboard.
 *
 * Three things, none of which a `<div>` with a backdrop click does on its own:
 *
 * - **Escape closes it.** Clicking the backdrop was the only way out, so a
 *   keyboard-only user had none.
 * - **Tab stays inside.** Without this, tabbing walks out of the dialog and
 *   into the page behind it -- which is still there, still scrollable, and
 *   gives no sign that focus has left the thing on top.
 * - **Focus comes back.** On close it returns to whatever opened the dialog,
 *   rather than resetting to the top of the document and making the reader
 *   find their place again.
 *
 * Deliberately a hook rather than a `<Modal>` component: the overlays here
 * already have their own markup and animation, and the part they were missing
 * is behaviour, not structure. The four layout drawers need exactly this too.
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

    // No visibility filter. `offsetParent` is the usual proxy for "actually
    // on screen" and it is always null under jsdom, which has no layout -- so
    // filtering on it would have emptied this list in every test while looking
    // right in a browser. A dialog's contents are visible by construction; the
    // `:not([disabled])` and `tabindex="-1"` exclusions in the selector are the
    // ones that matter.
    const focusables = () => Array.from(node.querySelectorAll(FOCUSABLE))

    // Focus the first thing in the dialog, or the dialog itself if it holds
    // nothing focusable -- otherwise focus stays on the page behind and the
    // trap below has nothing to trap.
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
      // Wrap at both ends. `document.activeElement` rather than `e.target`
      // because focus can sit on the container itself.
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
      // Only if it is still in the document -- the opener may have been
      // unmounted by whatever the dialog did.
      if (restoreTo && document.contains(restoreTo)) restoreTo.focus?.()
    }
  }, [ref, onClose, active])
}
