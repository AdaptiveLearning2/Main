import { useSyncExternalStore } from 'react'
import { serverWaking, subscribe } from '../../lib/serverWake'

/** While a request waits on a sleeping API, says so; otherwise renders nothing. */
export default function ServerWaking() {
  const waking = useSyncExternalStore(subscribe, () => serverWaking())
  if (!waking) return null
  return (
    <div role="status"
      className="fixed bottom-4 left-1/2 -translate-x-1/2 z-50 max-w-sm px-4 py-3 rounded-xl shadow-lg text-sm font-medium bg-amber-50 text-amber-900 border border-amber-200 dark:bg-amber-950 dark:text-amber-100 dark:border-amber-800">
      Starting the server — this can take up to a minute after a quiet spell.
    </div>
  )
}
