import { useEffect } from 'react'

// Everything focusable, minus anything explicitly taken out of the tab order.
const FOCUSABLE = [
  'a[href]', 'button:not([disabled])', 'input:not([disabled])',
  'select:not([disabled])', 'textarea:not([disabled])', '[tabindex]:not([tabindex="-1"])',
].join(',')

/**
 * Keyboard dialog behaviour for an overlay: Escape closes, Tab is trapped,
 * and focus returns to the opener on close.
 * @param onClose  called on Escape; should be stable or memoised
 * @param active   whether the dialog is open. Nothing is bound when false.
 */
export default function useDialog(ref, onClose, active = true) {
  useEffect(() => {
    if (!active) return undefined
    const node = ref.current
    if (!node) return undefined

    // Captured before focus moves: the opener.
    const restoreTo = document.activeElement

    // No `offsetParent` visibility filter: always null under jsdom.
    const focusables = () => Array.from(node.querySelectorAll(FOCUSABLE))

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
      // Not `e.target`: focus can be on the container itself.
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
      // The opener may have been unmounted.
      if (restoreTo && document.contains(restoreTo)) restoreTo.focus?.()
    }
  }, [ref, onClose, active])
}
