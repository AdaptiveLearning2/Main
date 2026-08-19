/** Read and write one browser-local preference, without letting storage break a page.
 *
 * `localStorage` **throws** rather than returning null when it is unavailable —
 * Safari private browsing, a browser with site data blocked, an iframe with
 * third-party storage partitioned off. Every access needs a guard, and by the
 * time this was extracted the same try/catch had been written three times:
 * `viewPrefs.js`, `useCollapsedSidebar.js`, and — the reason it matters —
 * `ThemeContext.jsx`, which had **no guard at all**, so an unavailable
 * `localStorage` threw during the provider's own `useState` initialiser and took
 * down every route in the application.
 *
 * A preference that cannot be saved is a small problem. A preference that
 * cannot be *read* without throwing is the whole app.
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
