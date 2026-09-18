import { useCallback, useMemo, useState } from 'react'

/**
 * Which measurements a signal chart is currently drawing.
 *
 * **It holds only the hidden keys and takes no series list**, and both halves
 * of that are deliberate.
 *
 * Storing what is *hidden* rather than what is shown is what keeps a series
 * that arrives later visible. These charts gain series as data resolves: a
 * report page that loads with no heart readings and then resolves its trend
 * turns `heart_rate_bpm` from unavailable into available. Held as a set of
 * shown keys, that series would be missing from a selection made before it
 * existed and would stay switched off with nothing on screen explaining why.
 * Held as a set of hidden ones, anything new is drawn by default and only an
 * explicit click ever takes a line away.
 *
 * Taking no list is what keeps the hook callable unconditionally. Which series
 * exist depends on loaded data — `hasHeart` is derived from the rows — and on
 * `SessionReview` that is computed well below the component's `loading` and
 * `err` early returns. A hook that needed the list had to be called there too,
 * which is a conditional hook call: React counts hooks by call order, so the
 * first render that returns early leaves every later one misaligned. It threw
 * on all 28 tests in that file. Holding state alone, it sits at the top of the
 * component with the other hooks and `shownOf` is applied wherever the list
 * finally exists.
 *
 * The selection is per mount and deliberately not persisted. `viewPrefs.js`
 * persists the teacher's *"Hide sensor data"* switch because that is a
 * standing preference about a whole page; this is a look at one chart, and
 * CLAUDE.md records what persisted view state costs — every test declared
 * after one that flips it inherits the flipped state, silently and only for
 * the tests written later.
 *
 * @returns {{hidden: Set<string>, toggle: Function, showAll: Function, shownOf: Function}}
 */
export function useSeriesFilter() {
  const [hidden, setHidden] = useState(() => new Set())

  const toggle = useCallback((key) => {
    setHidden((prev) => {
      // A new Set rather than a mutation: React compares by identity, so
      // mutating and returning the same object renders nothing.
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
