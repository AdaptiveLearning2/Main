import { vi } from 'vitest'

/**
 * The shared `apiFetch` mock: a router, so one of several parallel fetches can fail.
 *
 *     vi.mock('../../lib/api', async () => await import('../../test/mocks/apiFetch'))
 *     import { apiFetch, mockApi, resetApi, apiError } from '../../test/mocks/apiFetch'
 */

const routes = []

/** An unmatched path throws: a silent `undefined` would read as an empty success. */
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

/** A real `vi.fn()`. Reset with `resetApi()`, never `mockReset()`, which drops the routes. */
export const apiFetch = vi.fn(async (path, opts = {}) => {
  for (const entry of routes) {
    if (!matches(entry, path, opts)) continue
    return typeof entry.handler === 'function'
      ? await entry.handler(path, opts)
      : entry.handler
  }
  throw noRoute(path, opts?.method || 'GET')
})

/**
 * Object form `{ '/api/x': v, 'PUT /api/x': v }`, or array form
 * `[{ match: /regex/ | fn, handler, method }]`.
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

/** Replace the route table. Method-scoped routes are tried before methodless ones. */
export function mockApi(spec) {
  const entries = normalize(spec)
  routes.length = 0
  routes.push(...entries.filter(e => e.method), ...entries.filter(e => !e.method))
}

/** Layer one route over the table, taking precedence. */
export function overrideApi(match, handler, method = undefined) {
  routes.unshift({ match, handler, method })
}

/** Drop the routes and the recorded calls, keeping the implementation. */
export function resetApi() {
  routes.length = 0
  apiFetch.mockClear()
}

/** An error shaped like the real `apiFetch`'s, carrying `.status`. */
export function apiError(status, message) {
  const err = new Error(message ?? `HTTP ${status}`)
  err.status = status
  return err
}

/** A never-settling response, for the loading state. */
export function pending() {
  return () => new Promise(() => {})
}
