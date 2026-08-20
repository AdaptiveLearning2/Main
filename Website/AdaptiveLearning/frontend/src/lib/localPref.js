/** Read and write one browser-local preference, without letting storage break a page.
 *
 * `localStorage` can throw instead of returning null — Safari private
 * browsing, blocked site data, a partitioned iframe. Every access needs a
 * try/catch, or an unavailable `localStorage` can crash a component during
 * render and take down the whole app.
 */

/** The stored string, or `fallback` when there is nothing to read or reading throws. */
export function readPref(key, fallback = null) {
  try {
    const v = localStorage.getItem(key)
    return v === null ? fallback : v
  } catch {
    return fallback
  }
}

/** Store a string. Silently does nothing when storage is unavailable.
 *
 * Deliberately silent: the caller has no better answer than carrying on, and
 * every call site here is a layout preference rather than anything a user would
 * be told about.
 */
export function writePref(key, value) {
  try {
    localStorage.setItem(key, String(value))
  } catch {
    /* preference not persisted; the page still works */
  }
}

/** The `true`/`false` forms, since most of these are switches. */
export function readBoolPref(key, fallback = false) {
  const v = readPref(key)
  return v === null ? fallback : v === 'true'
}

/** Remove a key. Silently does nothing when storage is unavailable. */
export function clearPref(key) {
  try {
    localStorage.removeItem(key)
  } catch {
    /* nothing to clean up if storage is unavailable */
  }
}
