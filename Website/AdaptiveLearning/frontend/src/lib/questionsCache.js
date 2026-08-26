import { apiFetch } from './api'

// Analytics and Questions both fetch the full question bank independently on
// every mount -- two redundant reads of the same data during ordinary
// teacher navigation. Module-level state (not component state) is what lets
// this be shared across those two separate component instances.
const TTL_MS = 30_000 // matches backend QUESTIONS_CACHE_TTL default

let cached = null    // { limit, data, expiresAt }
let inFlight = null  // { limit, promise } -- shared while a fetch is in flight

export function fetchQuestionsCached(limit = 1000) {
  const now = Date.now()
  if (cached && cached.limit === limit && cached.expiresAt > now) {
    return Promise.resolve(cached.data)
  }
  if (inFlight && inFlight.limit === limit) {
    return inFlight.promise
  }
  const promise = apiFetch(`/api/questions?limit=${limit}`)
    .then(data => {
      cached = { limit, data, expiresAt: Date.now() + TTL_MS }
      inFlight = null
      return data
    })
    // Not populating `cached` on failure means each page's existing retry()
    // still forces a genuine refetch rather than replaying a rejection.
    .catch(err => { inFlight = null; throw err })
  inFlight = { limit, promise }
  return promise
}

// The cache is module-level state on purpose (see above), which means it
// also persists across tests in the same file unless cleared. Any test that
// renders a page using `fetchQuestionsCached` should call this in
// `beforeEach`, or a later test can be served a still-fresh entry left
// behind by an earlier one instead of hitting its own mocked response.
export function _resetForTests() {
  cached = null
  inFlight = null
}
