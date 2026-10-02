import { useEffect, useLayoutEffect, useRef } from 'react'

/**
 * Call `fn(stopped)` now, then `intervalMs` after each call settles, so calls never overlap.
 * Paused while the tab is hidden unless `pauseWhenHidden` is false; runs at once on return.
 * A throw doubles the next wait up to `maxBackoffMs`. A new `key` restarts the poll at once,
 * and `stopped()` turns true once the run that made a call is superseded or unmounted.
 */
export default function usePoll(fn, { intervalMs, key = null, enabled = true,
                                      pauseWhenHidden = true, maxBackoffMs = null } = {}) {
  const fnRef = useRef(fn)
  const intervalRef = useRef(intervalMs)
  // Latest values without restarting the poll: an interval change applies at the next wait.
  useLayoutEffect(() => {
    fnRef.current = fn
    intervalRef.current = intervalMs
  })

  useEffect(() => {
    if (!enabled) return undefined
    let stopped = false
    let running = false
    let timer = null
    let delay = intervalRef.current
    const isStopped = () => stopped
    const hidden = () => pauseWhenHidden && document.hidden

    const run = async () => {
      timer = null
      if (stopped || running || hidden()) return
      running = true
      let failed = false
      try {
        await fnRef.current(isStopped)
      } catch {
        failed = true
      }
      running = false
      if (stopped) return
      const cap = maxBackoffMs ?? intervalRef.current
      delay = failed ? Math.min(Math.max(delay, intervalRef.current) * 2, cap)
                     : intervalRef.current
      // Hidden: wait for visibilitychange rather than a timer that would only skip.
      if (!hidden()) timer = setTimeout(run, delay)
    }

    const onVisible = () => {
      if (stopped || document.hidden) return
      delay = intervalRef.current
      // In flight: that call schedules the next one, so starting here would make two chains.
      if (running) return
      clearTimeout(timer)
      run()
    }

    if (pauseWhenHidden) document.addEventListener('visibilitychange', onVisible)
    run()
    return () => {
      stopped = true
      clearTimeout(timer)
      if (pauseWhenHidden) document.removeEventListener('visibilitychange', onVisible)
    }
  }, [enabled, pauseWhenHidden, maxBackoffMs, key])
}
