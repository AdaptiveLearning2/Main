import { useEffect, useState } from 'react'
import { apiFetch } from '../lib/api'

/**
 * `/api/topics` for `grade`: every topic's row (`{name, allowed}`), and whether the read failed.
 * `undefined` grade: not known yet, nothing asked. `null` or '': no grade set, served as grade 1.
 * `rows` is `null` while unknown or failed; a new `attempt` asks again.
 */
export function useGradeTopicsState(grade, attempt = 0) {
  const known = grade !== undefined
  const path = grade ? `/api/topics?grade=${encodeURIComponent(grade)}` : '/api/topics'
  const key = `${path}#${attempt}`
  const [loaded, setLoaded] = useState({ key: null, rows: null, failed: false })
  useEffect(() => {
    if (!known) return
    let cancelled = false
    apiFetch(path)
      .then(rows => { if (!cancelled) setLoaded({ key, rows: rows || [], failed: false }) })
      .catch(() => { if (!cancelled) setLoaded({ key, rows: null, failed: true }) })
    return () => { cancelled = true }
  }, [known, path, key])
  const current = known && loaded.key === key
  return { rows: current ? loaded.rows : null, failed: current && loaded.failed }
}

/** The names of the topics `grade` may be served, or `null` while that is not known. */
export default function useGradeTopics(grade) {
  const { rows } = useGradeTopicsState(grade)
  return rows ? rows.filter(r => r.allowed).map(r => r.name) : null
}
