import { useCallback, useEffect, useState } from 'react'

/**
 * Load one admin resource, and mutate it, with the same three states each time.
 *
 * Shared by `AdminFlags` and `AdminSchoolYear`, which each used to write out
 * their own copy of this state machine and drifted (one cleared `error` on
 * save, the other didn't).
 *
 * `error` and `data` are kept independent on purpose: a failed refresh
 * leaves the last good payload in place, so the caller can show the error
 * as a banner over live data instead of blanking the page.
 *
 * @param load    a function returning a promise of the payload. Memoise it, or
 *                the effect below re-runs on every render.
 * @param pollMs  re-read on an interval, for values (like the consent
 *                bypass) that can expire on the clock without a write.
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

  /**
   * Run a write and adopt what it returns. Resolves to true on success, or
   * false on failure (never throws), so callers render the error instead of
   * catching it themselves.
   */
  const mutate = useCallback(async (write) => {
    setBusy(true)
    setError(null)
    try {
      setData(await write())
      return true
    } catch (e) {
      setError(e.message)
      return false
    } finally {
      setBusy(false)
    }
  }, [])

  return { data, setData, busy, error, refresh, mutate }
}
