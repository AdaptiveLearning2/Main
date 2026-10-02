import { useCallback, useEffect, useState } from 'react'

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

  useEffect(() => { refresh() }, [refresh])

  useEffect(() => {
    if (!pollMs) return undefined
    const t = setInterval(refresh, pollMs)
    return () => clearInterval(t)
  }, [refresh, pollMs])

  /** Run a write and adopt its result. Resolves true/false; never throws. */
  const mutate = useCallback(async (write) => {
    setBusy(true)
    setError(null)
    let ok = true
    try {
      setData(await write())
    } catch (e) {
      setError(e.message)
      ok = false
    }
    setBusy(false)
    return ok
  }, [])

  return { data, setData, busy, error, refresh, mutate }
}
