/** Browser-local preferences. `localStorage` can throw, so every access is guarded. */

/** The stored string, or `fallback` when there is nothing to read or reading throws. */
export function readPref(key, fallback = null) {
  try {
    const v = localStorage.getItem(key)
    return v === null ? fallback : v
  } catch {
    return fallback
  }
}

/** Store a string. Silently does nothing when storage is unavailable. */
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
