/**
 * Work that has to reach the backend before the session token is gone.
 *
 * A page that releases server state when it unmounts cannot do that on
 * sign-out: `signOut()` clears the stored session first and navigation
 * unmounts the page after, so the release goes out with no bearer and 401s.
 * `Adaptive.jsx` ending its session that way left it open, and under pull
 * the poller recording and holding the headband, until the 6 h sweep.
 *
 * `AuthContext.signOut` runs these first. Bounded, because sign-out must not
 * hang on a backend that does not answer: a task still running when the bound
 * passes is abandoned, and the page's unmount cleanup is the fallback it
 * always was.
 */
const tasks = new Set()

export const SIGN_OUT_TASK_TIMEOUT_MS = 4000

/** Register `fn` to run before sign-out. Returns the unregister function, so
 *  it can be an effect's cleanup as it stands. */
export function onSignOut(fn) {
  tasks.add(fn)
  return () => { tasks.delete(fn) }
}

/** Run every registered task, settling when all have or the bound passes.
 *  Never rejects: a task that fails must not stop the sign-out. */
export function runSignOutTasks(timeoutMs = SIGN_OUT_TASK_TIMEOUT_MS) {
  const all = Promise.allSettled([...tasks].map(fn => {
    try { return fn() } catch (e) { return Promise.reject(e) }
  }))
  let timer
  const bound = new Promise(resolve => { timer = setTimeout(resolve, timeoutMs) })
  return Promise.race([all, bound]).finally(() => clearTimeout(timer))
}
