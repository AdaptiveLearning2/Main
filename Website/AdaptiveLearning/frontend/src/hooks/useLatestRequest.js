import { useCallback, useRef } from 'react'

/**
 * Guards against a superseded response overwriting the one that replaced it.
 *
 * `begin()` claims the request and hands back `isCurrent()`, true only while
 * nothing newer has been started. Check it at **every** await, not just the
 * first: the point of the guard is the slow middle of a fan-out, which is the
 * window a user actually switches context in.
 *
 *     const begin = useLatestRequest()
 *     const isCurrent = begin()
 *     const rows = await apiFetch(...)
 *     if (!isCurrent()) return
 *
 * A generation counter rather than a cleanup flag, because these fetches have
 * more than one caller — an effect and a retry button — and a flag owned by the
 * effect leaves the retry unguarded. It is a ref, so claiming a request never
 * causes a render.
 */
export function useLatestRequest() {
  const generation = useRef(0)
  return useCallback(() => {
    const mine = ++generation.current
    return () => mine === generation.current
  }, [])
}
