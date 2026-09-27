import { apiFetch } from './api'

// Module-level question-bank cache shared across pages, keyed by `limit`.
const TTL_MS = 30_000 // matches backend QUESTIONS_CACHE_TTL default

const cache = new Map()     // limit -> { data, expiresAt }
const inFlight = new Map()  // limit -> promise, while a fetch is in flight

export function fetchQuestionsCached(limit = 1000) {
  const now = Date.now()
  const hit = cache.get(limit)
  if (hit && hit.expiresAt > now) {
    return Promise.resolve(hit.data)
  }
  const pending = inFlight.get(limit)
  if (pending) {
    return pending
  }
  const promise = apiFetch(`/api/questions?limit=${limit}`)
    .then(data => {
      cache.set(limit, { data, expiresAt: Date.now() + TTL_MS })
      inFlight.delete(limit)
      return data
    })
    // Failures are not cached, so a retry refetches.
    .catch(err => { inFlight.delete(limit); throw err })
  inFlight.set(limit, promise)
  return promise
}

/**
 * The bank's exact size, overall and per difficulty, from `/api/questions/count`: the list above
 * is capped at 1000 rows by PostgREST, so its length is never the total. Null where a count failed.
 */
export async function fetchQuestionCounts() {
  const [total, easy, medium, hard] = await Promise.all(
    [null, 'easy', 'medium', 'hard'].map(fetchQuestionCount))
  return { total, easy, medium, hard }
}

/** One exact count, optionally for a difficulty; null if it failed. Never throws. */
export function fetchQuestionCount(difficulty = null) {
  return apiFetch(`/api/questions/count${difficulty ? `?difficulty=${difficulty}` : ''}`)
    .then(r => (r?.retrieved === false ? null : r?.total ?? null))
    .catch(() => null)
}

// Call in `beforeEach` of any test rendering a page that uses the cache.
export function _resetForTests() {
  cache.clear()
  inFlight.clear()
}
