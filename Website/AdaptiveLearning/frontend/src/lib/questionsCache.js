import { apiFetch } from './api'

// Analytics and Questions both fetch the full question bank independently on
// every mount -- two redundant reads of the same data during ordinary
// teacher navigation. Module-level state (not component state) is what lets
// this be shared across those two separate component instances.
//
// Keyed by `limit` rather than a single slot: a single slot would only help
// callers sharing the exact same limit, and a second caller with a different
// limit (e.g. Dashboard.jsx's `?limit=5`) would thrash the first one's entry
// on every call instead of getting its own.
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
    // Not populating `cache` on failure means each page's existing retry()
    // still forces a genuine refetch rather than replaying a rejection.
    .catch(err => { inFlight.delete(limit); throw err })
  inFlight.set(limit, promise)
  return promise
}

// The cache is module-level state on purpose (see above), which means it
// also persists across tests in the same file unless cleared. Any test that
// renders a page using `fetchQuestionsCached` should call this in
// `beforeEach`, or a later test can be served a still-fresh entry left
// behind by an earlier one instead of hitting its own mocked response.
export function _resetForTests() {
  cache.clear()
  inFlight.clear()
}
