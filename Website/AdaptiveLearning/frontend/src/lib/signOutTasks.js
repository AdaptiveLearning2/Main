/**
 * Work that must reach the backend before sign-out clears the token; unmount
 * cleanup runs after, with no bearer, and 401s. `AuthContext.signOut` runs
 * these first, bounded so sign-out cannot hang; unmount cleanup stays the fallback.
 */
const tasks = new Set()

export const SIGN_OUT_TASK_TIMEOUT_MS = 4000

/** Register `fn` to run before sign-out. Returns the unregister function. */
export function onSignOut(fn) {
  tasks.add(fn)
  return () => { tasks.delete(fn) }
}

/** Run every task; settles when all have or the bound passes. Never rejects. */
export function runSignOutTasks(timeoutMs = SIGN_OUT_TASK_TIMEOUT_MS) {
  const all = Promise.allSettled([...tasks].map(fn => {
    try { return fn() } catch (e) { return Promise.reject(e) }
  }))
  let timer
  const bound = new Promise(resolve => { timer = setTimeout(resolve, timeoutMs) })
  return Promise.race([all, bound]).finally(() => clearTimeout(timer))
}
