import { vi } from 'vitest'

/**
 * The shared `apiFetch` mock.
 *
 * Use it by pointing the module factory straight at this file, which keeps the
 * mocked module and the handle the test drives it with as one instance:
 *
 *     vi.mock('../../lib/api', async () => await import('../../test/mocks/apiFetch'))
 *     import { apiFetch, mockApi, resetApi, apiError } from '../../test/mocks/apiFetch'
 *
 * A router, not a bare `vi.fn()`, because most pages fetch several endpoints
 * in parallel on mount and tests need one of them to fail. Chaining
 * `mockResolvedValueOnce` calls would depend on `Promise.all`'s call order,
 * which breaks the moment a page adds another fetch.
 */

const routes = []

/** An unmatched path throws rather than resolving undefined.
 *
 *  A silent `undefined` would read as a successful empty response, which is
 *  exactly the state most of this suite exists to distinguish from a
 *  failure — so throwing keeps a gap in test setup from masquerading as the
 *  bug under test.
 */
function noRoute(path, method) {
  const known = routes.length
    ? routes.map(r => `${r.method || 'ANY'} ${describeMatch(r.match)}`).join(', ')
    : '(none registered)'
  return new Error(
    `apiFetch mock has no route for ${method} ${path}.\n` +
    `Registered: ${known}\n` +
    `Add one with mockApi({'${path}': …}) or overrideApi('${path}', …).`)
}

function describeMatch(m) {
  if (typeof m === 'function') return m.name ? `fn:${m.name}` : 'fn'
  return String(m)
}

function matches(entry, path, opts) {
  if (entry.method && (opts?.method || 'GET') !== entry.method) return false
  const m = entry.match
  if (typeof m === 'function') return !!m(path, opts)
  if (m instanceof RegExp) return m.test(path)
  return m === path
}

/** Still a real `vi.fn()`, so `toHaveBeenCalledWith` keeps working. Reset
 *  with `resetApi()`, not `mockReset()` — the latter drops this
 *  implementation and every route with it. */
export const apiFetch = vi.fn(async (path, opts = {}) => {
  for (const entry of routes) {
    if (!matches(entry, path, opts)) continue
    return typeof entry.handler === 'function'
      ? await entry.handler(path, opts)
      : entry.handler
  }
  throw noRoute(path, opts?.method || 'GET')
})

/** Accepts either the object form, terser for the common case:
 *
 *     mockApi({ '/api/sessions': [], 'PUT /api/profile/me': { ok: true } })
 *
 *  or the array form, when a match needs a regex or a predicate:
 *
 *     mockApi([{ match: /^\/api\/stats\//, handler: () => STATS }])
 */
function normalize(spec) {
  if (Array.isArray(spec)) return spec.map(e => ({ ...e }))
  return Object.entries(spec || {}).map(([key, handler]) => {
    const [maybeMethod, ...rest] = key.split(' ')
    return rest.length
      ? { method: maybeMethod, match: rest.join(' '), handler }
      : { match: key, handler }
  })
}

/** Replace the route table. Call in `beforeEach` or at the top of a test.
 *
 *  Method-scoped routes are always tried before methodless ones, regardless
 *  of write order — otherwise `{'/api/profile/me': …, 'PUT /api/profile/me':
 *  …}` would have the read route silently answer the write too.
 */
export function mockApi(spec) {
  const entries = normalize(spec)
  routes.length = 0
  routes.push(...entries.filter(e => e.method), ...entries.filter(e => !e.method))
}

/** Layer one route over the table, taking precedence over what is there.
 *
 *  Lets a test start from the page's whole happy path and override just the
 *  one endpoint it needs to fail, instead of restating every route.
 */
export function overrideApi(match, handler, method = undefined) {
  routes.unshift({ match, handler, method })
}

/** Drop the routes and the recorded calls, keeping the implementation. */
export function resetApi() {
  routes.length = 0
  apiFetch.mockClear()
}

/** An error shaped like the one the real `apiFetch` throws.
 *
 *  `.status` is the part that matters — callers branch on it to tell "not
 *  found" from "request failed", and a bare `new Error()` would only
 *  exercise the second path.
 */
export function apiError(status, message) {
  const err = new Error(message ?? `HTTP ${status}`)
  err.status = status
  return err
}

/** A never-settling response, for testing what a page shows while it waits.
 *  Distinct from a rejection on purpose — "still loading" and "failed" are
 *  different states.
 */
export function pending() {
  return () => new Promise(() => {})
}
