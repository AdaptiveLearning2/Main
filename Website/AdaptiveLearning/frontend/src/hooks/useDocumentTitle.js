import { useEffect } from 'react'

const SUFFIX = 'AdaptiveLearning'

/** Set the document title for as long as this component is mounted.
 *
 * Without this every route shares the same static title from index.html,
 * so open tabs and history entries are indistinguishable, and a screen
 * reader never announces navigation in a single-page app.
 *
 * Restores the previous title on unmount, not just clearing it, so a
 * nested route with its own title doesn't blank the outer page's title.
 */
export default function useDocumentTitle(title) {
  useEffect(() => {
    if (!title) return
    const previous = document.title
    document.title = `${title} · ${SUFFIX}`
    return () => { document.title = previous }
  }, [title])
}
