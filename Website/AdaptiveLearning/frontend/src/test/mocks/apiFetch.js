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
 * A router rather than a bare `vi.fn()` because most pages here fetch two to
 * four endpoints in parallel on mount and the interesting tests need *one* of
 * them to fail. `mockResolvedValueOnce` chains express that by call order,
 * which is the order `Promise.all` happens to start them in -- so a test
 * written that way passes for a reason unrelated to what it claims, and breaks
 * when a page adds a fetch.
 */

const routes = []

/** An unmatched path throws rather than resolving undefined.
 *
 *  A silent `undefined` reaches the page as a successful read of nothing, which
 *  is the exact state most of this suite exists to tell apart from a failure --
 *  so a gap in a test's own setup would arrive dressed as the bug being tested
 *  for. Loud, with the routes it did have, so the fix is obvious.
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

/** Still a real `vi.fn()`, so `toHaveBeenCalledWith` keeps working -- the
 *  routing is how a response is *chosen*, not a replacement for asserting on
 *  the call. Note `resetApi()` rather than `mockReset()`: the latter drops this
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
 *  Method-scoped routes are tried before methodless ones, whatever order they
 *  were written in. First-match-wins alone made this depend on object key
 *  order: `{'/api/profile/me': …, 'PUT /api/profile/me': …}` had the read
 *  answering the write, and the fix would have been to reorder two keys --
 *  which is not a rule anyone would infer, and fails silently by returning a
 *  plausible payload for the wrong verb.
 */
export function mockApi(spec) {
  const entries = normalize(spec)
  routes.length = 0
  routes.push(...entries.filter(e => e.method), ...entries.filter(e => !e.method))
}

/** Layer one route over the table, taking precedence over what is there.
 *
 *  This is the point of the router: a test that needs a single endpoint to fail
 *  starts from the page's whole happy path and overrides one entry, instead of
 *  restating every endpoint the page happens to call.
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
 *  `.status` is the part that matters: callers that tell "this doesn't exist"
 *  apart from "the request failed" branch on it, and a bare `new Error()` in a
 *  test silently exercises only the second path.
 */
export function apiError(status, message) {
  const err = new Error(message ?? `HTTP ${status}`)
  err.status = status
  return err
}

/** A never-settling response, for testing what a page shows while it waits.
 *
 *  Distinct from a rejection on purpose -- "still loading" and "failed" are
 *  different states and several pages here have been wrong about exactly that.
 */
export function pending() {
  return () => new Promise(() => {})
}
