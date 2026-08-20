import { useCallback, useEffect, useState } from 'react'
import { readBoolPref, writePref } from '../lib/localPref'

/** Whether one layout's desktop sidebar is collapsed, remembered across reloads.
 *
 * Persisted to storage rather than plain component state, since state
 * survives navigation but not a page refresh.
 *
 * `scope` is required so each layout gets its own key — sharing one key
 * would leak the collapse state between roles on a shared machine.
 *
 * Kept per-browser rather than server-side: it describes one window's
 * layout, not the student, so it isn't worth a round trip like
 * `difficulty_bias` and the other server-stored preferences are.
 */
export default function useCollapsedSidebar(scope) {
  if (!scope) {
    // Throw loudly — a missing scope would silently merge every layout onto
    // one key, the exact bug this parameter exists to prevent.
    throw new Error('useCollapsedSidebar needs a scope, e.g. "teacher"')
  }
  const key = `al_sidebar_collapsed:${scope}`

  // Lazy initializer so the read happens once at mount, not on every render.
  const [collapsed, setCollapsed] = useState(() => readBoolPref(key))

  useEffect(() => { writePref(key, collapsed) }, [key, collapsed])

  const toggle = useCallback(() => setCollapsed(c => !c), [])

  return [collapsed, toggle]
}
