import { useCallback, useEffect, useState } from 'react'

const KEY = 'al_sidebar_collapsed'

/** Whether the desktop sidebar is collapsed, remembered across reloads.
 *
 * It was `useState(false)` in each of the four layouts, which survives
 * navigation — the layout stays mounted under its child routes — and not a
 * refresh. So a teacher who collapsed it to get more width for the live monitor
 * had it back at 240px on the next page load, every time, with no way to make
 * the choice stick.
 *
 * Deliberately *not* a server-side preference on `profiles`, unlike
 * `difficulty_bias` and the two beside it. Those describe the student and have
 * to reach the backend because the backend acts on them; this describes one
 * browser window's layout, is worth nothing on another device, and a round trip
 * per person per session to remember a sidebar width would be the wrong trade.
 * `viewPrefs.js` is the precedent for a genuinely viewer-side preference.
 *
 * Read once at mount rather than on every render, and every access is guarded:
 * `localStorage` throws rather than returning null in a private window with
 * storage disabled, and an unreadable preference must not take a layout —
 * meaning the whole application — down with it.
 */
function read() {
  try {
    return localStorage.getItem(KEY) === 'true'
  } catch {
    return false
  }
}

export default function useCollapsedSidebar() {
  // Lazy initialiser, so the read happens once at mount and not on every
  // render of a component that wraps every page in the app.
  const [collapsed, setCollapsed] = useState(read)

  useEffect(() => {
    try {
      localStorage.setItem(KEY, String(collapsed))
    } catch {
      // Nothing to do and nothing to say. The preference not persisting is a
      // smaller problem than a layout that throws.
    }
  }, [collapsed])

  const toggle = useCallback(() => setCollapsed(c => !c), [])

  return [collapsed, toggle]
}
