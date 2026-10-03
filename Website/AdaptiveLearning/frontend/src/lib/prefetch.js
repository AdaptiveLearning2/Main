import { useEffect } from 'react'

/** The reader asked to save data, or is on a 2G-class link: a guessed download is not worth it. */
function saveData() {
  const connection = typeof navigator === 'undefined' ? null : navigator.connection
  return connection?.saveData === true || ['slow-2g', '2g'].includes(connection?.effectiveType)
}

/**
 * Call `loader` once the browser is idle (after 2 s where requestIdleCallback is missing,
 * as on Safari), unless the reader is saving data. Failures are ignored: it only warms a chunk.
 * Returns a cancel function.
 */
export function prefetchWhenIdle(loader) {
  if (saveData()) return () => {}
  const run = () => { loader().catch(() => {}) }
  if (typeof window.requestIdleCallback === 'function') {
    const id = window.requestIdleCallback(run, { timeout: 5000 })
    return () => window.cancelIdleCallback?.(id)
  }
  const timer = setTimeout(run, 2000)
  return () => clearTimeout(timer)
}

/** Warm the chunk of the page a reader most likely opens next, once this one has settled. */
export function usePrefetchWhenIdle(loader) {
  useEffect(() => prefetchWhenIdle(loader), [loader])
}
