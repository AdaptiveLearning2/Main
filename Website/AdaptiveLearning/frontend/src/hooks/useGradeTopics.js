import { useEffect, useState } from 'react'
import { apiFetch } from '../lib/api'

/**
 * The topics `grade` may be served, from `/api/topics` (the backend's `_allowed_topics`).
 * `null` while unknown: no grade yet, a failed read, or an answer for a previous grade.
 */
export default function useGradeTopics(grade) {
  const [loaded, setLoaded] = useState({ grade: null, topics: null })
  useEffect(() => {
    if (!grade) return
    let cancelled = false
    apiFetch(`/api/topics?grade=${encodeURIComponent(grade)}`)
      .then(rows => {
        if (!cancelled) setLoaded({ grade, topics: (rows || []).filter(r => r.allowed).map(r => r.name) })
      })
      .catch(() => { if (!cancelled) setLoaded({ grade, topics: null }) })
    return () => { cancelled = true }
  }, [grade])
  return loaded.grade === grade ? loaded.topics : null
}
