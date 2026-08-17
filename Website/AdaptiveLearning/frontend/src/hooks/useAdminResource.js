import { useCallback, useEffect, useState } from 'react'

/**
 * Load one admin resource, and mutate it, with the same three states each time.
 *
 * `AdminFlags` and `AdminSchoolYear` each wrote this out: a `busy` flag, an
 * `error` string, a fetch that catches into the error, a save that clears the
 * error and sets busy and clears busy in a `finally`, and the same two early
 * returns underneath. Two copies of a state machine is how one of them comes to
 * clear `error` on save and the other does not.
 *
 * **`error` and `data` are independent, deliberately.** A failed *refresh*
 * leaves the last good payload in place, so a page that was correct a moment
 * ago does not blank because one poll missed -- the caller decides whether to
 * render the error as a banner over live data or as the whole page, and it can
 * only make that choice if both are still here. The same reasoning as the
 * teacher dashboard keeping its questions on a failed refresh.
 *
 * @param load    a function returning a promise of the payload. Memoise it, or
 *                the effect below re-runs on every render.
 * @param pollMs  re-read on an interval. `AdminFlags` needs it because the
 *                consent bypass expires on the clock rather than on a write, so
 *                nothing tells the page it is over.
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
   * Run a write and adopt what it returns. Resolves true if it landed, so a
   * caller can show its own "saved" confirmation without repeating the
   * try/catch -- and false rather than throwing, because every caller here
   * wants the error rendered rather than propagated.
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
