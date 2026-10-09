import { useId, useRef, useState } from 'react'
import { Info } from 'lucide-react'

/**
 * A "what is this?" disclosure: a button that shows a short explanation below it.
 * Click or keyboard opens it, Escape closes it and returns focus; never hover-only.
 * `contents`: in a `flex-wrap` row the panel takes its own full-width line.
 */
export default function InfoTip({ label, children }) {
  const [open, setOpen] = useState(false)
  const panelId = useId()
  const buttonRef = useRef(null)

  function onKeyDown(e) {
    if (e.key === 'Escape' && open) {
      e.stopPropagation()
      setOpen(false)
      buttonRef.current?.focus()
    }
  }

  return (
    <span className="contents" onKeyDown={onKeyDown}>
      <button
        ref={buttonRef}
        type="button"
        aria-expanded={open}
        aria-controls={panelId}
        onClick={() => setOpen(o => !o)}
        className="inline-flex items-center justify-center min-w-[44px] min-h-[44px] -m-3 rounded-full text-gray-600 dark:text-gray-400 hover:text-gray-900 dark:hover:text-white"
      >
        <Info size={14} aria-hidden="true" />
        <span className="sr-only">What is {label}?</span>
      </button>
      <span
        id={panelId}
        hidden={!open}
        className="basis-full mt-2 block rounded-lg bg-slate-50 dark:bg-gray-800 p-2 text-xs font-normal normal-case tracking-normal text-gray-600 dark:text-gray-400"
      >
        {children}
      </span>
    </span>
  )
}
