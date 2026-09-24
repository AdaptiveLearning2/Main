import { useCallback, useMemo, useState } from 'react'

/**
 * Which measurements a signal chart is drawing. Stores the hidden keys, so a
 * series that arrives later is shown; takes no list, so it can be called above
 * early returns. Per mount, not persisted.
 *
 * @returns {{hidden: Set<string>, toggle: Function, showAll: Function, shownOf: Function}}
 */
export function useSeriesFilter() {
  const [hidden, setHidden] = useState(() => new Set())

  const toggle = useCallback((key) => {
    setHidden((prev) => {
      const next = new Set(prev)
      if (next.has(key)) next.delete(key)
      else next.add(key)
      return next
    })
  }, [])

  const showAll = useCallback(() => setHidden(new Set()), [])

  const shownOf = useCallback(
    (series) => (series || []).filter((s) => !hidden.has(s.key)),
    [hidden],
  )

  return useMemo(
    () => ({ hidden, toggle, showAll, shownOf }),
    [hidden, toggle, showAll, shownOf],
  )
}
