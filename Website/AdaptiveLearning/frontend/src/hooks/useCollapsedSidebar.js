import { useCallback, useEffect, useState } from 'react'
import { readBoolPref, writePref } from '../lib/localPref'

/**
 * Whether one layout's desktop sidebar is collapsed, persisted per browser.
 * `scope` is required so each layout (role) gets its own key.
 */
export default function useCollapsedSidebar(scope) {
  if (!scope) {
    throw new Error('useCollapsedSidebar needs a scope, e.g. "teacher"')
  }
  const key = `al_sidebar_collapsed:${scope}`

  const [collapsed, setCollapsed] = useState(() => readBoolPref(key))

  useEffect(() => { writePref(key, collapsed) }, [key, collapsed])

  const toggle = useCallback(() => setCollapsed(c => !c), [])

  return [collapsed, toggle]
}
