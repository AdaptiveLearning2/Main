import { useEffect, useRef, useState } from 'react'
import { markEegStarted } from '../lib/session'

// Backoff for a failed report, per session; after the last, only a change of `active` retries.
export const EEG_START_RETRY_MS = [5000, 30000, 120000]

/**
 * Push only: report once per session, while `active`, that a headband is streaming into it.
 * The backend never sees a push start, and its `signals_missing` alert needs to know. A failure
 * backs off; a re-run (a flapping link) sends at once and cancels the retry it would duplicate.
 */
export default function useEegStartReport(active, sessionId) {
  const reported = useRef(null)
  // Failures for the current session, and the pending retry; the timer outlives effect re-runs.
  const failures = useRef({ sessionId: null, count: 0 })
  const timer = useRef(null)
  // No clear on unmount: a late timer only sets state React drops, so nothing is sent.
  const [retry, setRetry] = useState(0)

  useEffect(() => {
    if (!active || !sessionId || reported.current === sessionId) return
    reported.current = sessionId
    if (failures.current.sessionId !== sessionId) failures.current = { sessionId, count: 0 }
    clearTimeout(timer.current)
    markEegStarted(sessionId).then(ok => {
      if (ok || reported.current !== sessionId) return
      reported.current = null
      const wait = EEG_START_RETRY_MS[failures.current.count++]
      if (wait != null) timer.current = setTimeout(() => setRetry(n => n + 1), wait)
    })
  }, [active, sessionId, retry])
}
