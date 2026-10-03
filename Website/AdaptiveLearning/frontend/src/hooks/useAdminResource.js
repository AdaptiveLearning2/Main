import { useCallback, useEffect, useState } from 'react'
import usePoll from './usePoll'

/**
 * Load and mutate one admin resource (`data`, `busy`, `error`).
 * A failed refresh keeps the last good `data`, so the error can sit over it.
 * @param load    returns a promise of the payload; memoise it, or the effect re-runs every render.
 * @param pollMs  re-read interval, for values (like the consent bypass) that expire without a write.
 */
export default function useAdminResource({ load, pollMs = 0 }) {
  const [data, setData] = useState(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)

  const refresh = useCallback(
    () => load().then(setData).catch(e => setError(e.message)),
    [load])

  // With `pollMs` the poll makes the first read too, so the page asks once, not twice.
  useEffect(() => { if (!pollMs) refresh() }, [refresh, pollMs])

  usePoll(async (stopped) => {
    try {
      const d = await load()
      if (!stopped()) setData(d)
    } catch (e) {
      if (!stopped()) setError(e.message)
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

  return { data, setData, busy, error, refresh, mutate }
}
