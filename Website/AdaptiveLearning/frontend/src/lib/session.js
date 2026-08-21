import { apiFetch } from './api'
import { toast } from 'sonner'

/**
 * Close a practice session.
 *
 * Shared by both practice pages so a failed close is never silently swallowed.
 * The daily rollup and chart archive both depend on this call succeeding, and
 * those raw rows get deleted at year end, so a lost close means a lost summary.
 *
 * Never throws: the stale-session sweep cleans up a failed close later, so the
 * page can reset its own state regardless. The caller decides what to do with
 * the page; this just tells the student what happened.
 *
 * @param {string} id  session id; a falsy id is a no-op, not an error
 * @returns {Promise<boolean>} whether the backend confirmed the close
 */
/**
 * Record one answer against a session.
 *
 * Shared by both question pages, which both POST to
 * `/api/sessions/{id}/answer` and need to handle a failure the same way.
 *
 * Never throws, so it's safe to call without awaiting — `Practice`'s
 * countdown does this from a timer callback with nothing to catch a rejection.
 *
 * Returns the backend's response so a caller can use it: `Adaptive` reads
 * `topic` off it to update its tallies without guessing which topic the
 * question belonged to. `null` means the answer did not land.
 *
 * Does not decide correctness — the two pages hold questions in different
 * shapes and compare them differently, so `correct` stays the caller's call.
 *
 * @returns {Promise<object|null>} the response, or null if nothing was recorded
 */
export async function recordAnswer({ sessionId, questionId, selectedIndex, correct }) {
  // Loud rather than silent: an answer with no session or question id can't
  // be attributed to anything, so this must not fail invisibly.
  if (!sessionId || !questionId) {
    console.error('[answer] not recorded', { session: sessionId, question: questionId })
    toast.error('That answer could not be saved.')
    return null
  }
  try {
    return await apiFetch(`/api/sessions/${sessionId}/answer`, {
      method: 'POST',
      body: { question_id: questionId, selected_index: selectedIndex, correct },
    })
  } catch (e) {
    console.error('[answer] not recorded', e)
    toast.error('That answer could not be saved.')
    return null
  }
}

export async function endSession(id) {
  if (!id) return false
  try {
    await apiFetch(`/api/sessions/${id}/end`, { method: 'POST' })
    return true
  } catch (e) {
    console.error('[session] could not end', e)
    toast.error('Could not close the session cleanly — it will be tidied up next time you practise.')
    return false
  }
}
