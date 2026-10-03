// Whether the API looks asleep: Render's free tier stops it after ~15 idle minutes and the
// next request waits out its boot. Its own module, since many tests replace `lib/api` whole.

/** A request pending this long, with no recent answer, reads as a sleeping server. */
export const WAKE_AFTER_MS = 3000
/** An answer this recent means the server is up, whatever one slow request is doing. */
export const AWAKE_FOR_MS = 10 * 60_000

const inflight = new Map()     // request id -> start time (ms)
let lastAnswerAt = null
let nextId = 0
const listeners = new Set()
const notify = () => listeners.forEach(listener => listener())

/** Call as a request goes out; call what it returns with whether any HTTP answer came back. */
export function startRequest() {
  const id = ++nextId
  inflight.set(id, Date.now())
  // No event marks the threshold passing, so re-check listeners when it does.
  const timer = setTimeout(notify, WAKE_AFTER_MS)
  return (answered) => {
    clearTimeout(timer)
    inflight.delete(id)
    if (answered) lastAnswerAt = Date.now()
    notify()
  }
}

export function serverWaking(now = Date.now()) {
  if (lastAnswerAt !== null && now - lastAnswerAt < AWAKE_FOR_MS) return false
  for (const started of inflight.values()) if (now - started >= WAKE_AFTER_MS) return true
  return false
}

export function subscribe(listener) {
  listeners.add(listener)
  return () => listeners.delete(listener)
}

export function _resetForTests() {
  inflight.clear()
  lastAnswerAt = null
}
