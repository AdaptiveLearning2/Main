import { useCallback, useRef } from 'react'

/**
 * Guards against a superseded response overwriting a newer one: `const isCurrent = begin()`,
 * then `if (!isCurrent()) return` after every await. A generation counter, not a cleanup
 * flag, so a retry button is guarded too.
 */
export function useLatestRequest() {
  const generation = useRef(0)
  return useCallback(() => {
    const mine = ++generation.current
    return () => mine === generation.current
  }, [])
}
