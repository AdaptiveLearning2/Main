import { useEffect } from 'react'

const SUFFIX = 'AdaptiveLearning'

/** Set the document title for as long as this component is mounted.
 *
 * Every route rendered the same static title from index.html, so browser
 * history, bookmarks and open tabs were indistinguishable from one another --
 * a teacher with Live, a class and a student report open saw three identical
 * tabs. It is also the accessible name a screen reader announces on
 * navigation, so a single-page app with one title never announces the move.
 *
 * Restores the previous title on unmount rather than clearing it, so a route
 * that renders one nested inside a route that also does cannot leave the
 * outer page nameless on the way back out.
 */
export default function useDocumentTitle(title) {
  useEffect(() => {
    if (!title) return
    const previous = document.title
    document.title = `${title} · ${SUFFIX}`
    return () => { document.title = previous }
  }, [title])
}
