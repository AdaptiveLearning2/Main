import { useCallback, useEffect, useState } from 'react'
import { readBoolPref, writePref } from '../lib/localPref'

/** Whether one layout's desktop sidebar is collapsed, remembered across reloads.
 *
 * It was `useState(false)` in each of the four layouts, which survives
 * navigation — the layout stays mounted under its child routes — and not a
 * refresh. So a teacher who collapsed it to get more width for the live monitor
 * had it back at 240px on the next page load, every time.
 *
 * **`scope` is required, and the four layouts pass different values.** A single
 * key would be shared by every layout in the origin, so on a shared school
 * machine a teacher collapsing their sidebar would collapse the next student's
 * too. The sidebars are not the same control — they hold different navigation
 * and different numbers of items — so one is not a sensible answer for the
 * other, and the leak reads as the app losing a setting rather than as one
 * being shared.
 *
 * It is still keyed per *browser*, not per account: two people sharing a
 * profile still share the preference within a role. Narrowing that further
 * means storing it server-side, which is the wrong trade for a sidebar width —
 * see below.
 *
 * Deliberately **not** a server-side preference on `profiles`, unlike
 * `difficulty_bias` and the two beside it. Those describe the student and have
 * to reach the backend because the backend acts on them; this describes one
 * browser window's layout, is worth nothing on another device, and a round trip
 * per person per session to remember a sidebar width would be the wrong trade.
 * `viewPrefs.js` is the precedent for a genuinely viewer-side preference.
 */
export default function useCollapsedSidebar(scope) {
  if (!scope) {
    // Loud, because the failure is silent otherwise: a missing scope would
    // quietly reunite every layout on one key, which is the bug this parameter
    // exists to prevent.
    throw new Error('useCollapsedSidebar needs a scope, e.g. "teacher"')
  }
  const key = `al_sidebar_collapsed:${scope}`

  // Lazy initialiser, so the read happens once at mount and not on every render
  // of a component that wraps every page in the app.
  const [collapsed, setCollapsed] = useState(() => readBoolPref(key))

  useEffect(() => { writePref(key, collapsed) }, [key, collapsed])

  const toggle = useCallback(() => setCollapsed(c => !c), [])

  return [collapsed, toggle]
}
