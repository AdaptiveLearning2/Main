import { useCallback, useEffect, useState } from 'react'
import usePoll from './usePoll'

/**
 * Load and mutate one admin resource. A failed refresh keeps the last good `data`. `error` is a
 * message (reads and writes); `loadError` is the last read's Error with `.status`, cleared by a good read.
 * `load` must be memoised (or the effect re-runs every render); `pollMs` re-reads values that expire.
 */
export default function useAdminResource({ load, pollMs = 0 }) {
  const [data, setData] = useState(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const [loadError, setLoadError] = useState(null)

  const loaded = useCallback(d => { setData(d); setLoadError(null) }, [])
  const failed = useCallback(e => { setError(e.message); setLoadError(e) }, [])

  const refresh = useCallback(() => load().then(loaded).catch(failed), [load, loaded, failed])

  // With `pollMs` the poll makes the first read too, so the page asks once, not twice.
  useEffect(() => { if (!pollMs) refresh() }, [refresh, pollMs])

  usePoll(async (stopped) => {
    try {
      const d = await load()
      if (!stopped()) loaded(d)
    } catch (e) {
      if (!stopped()) failed(e)
    }
  }, { intervalMs: pollMs, enabled: pollMs > 0, key: load })

  /** Run a write and adopt its result. Resolves true/false; never throws. */
  const mutate = useCallback((write) => {
    setBusy(true)
    setError(null)
    return Promise.resolve()
      .then(() => write())
      .then(result => { setData(result); return true })
      // A rejection that is not an Error must still resolve, not throw from here.
      .catch(e => { setError(e?.message ?? String(e)); return false })
      .finally(() => setBusy(false))
  }, [])

  return { data, setData, busy, error, loadError, refresh, mutate }
}
