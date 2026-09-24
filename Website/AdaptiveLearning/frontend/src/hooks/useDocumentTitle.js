import { useEffect } from 'react'

const SUFFIX = 'AdaptiveLearning'

/** Set the document title while mounted; restores the previous title on unmount. */
export default function useDocumentTitle(title) {
  useEffect(() => {
    if (!title) return
    const previous = document.title
    document.title = `${title} · ${SUFFIX}`
    return () => { document.title = previous }
  }, [title])
}
