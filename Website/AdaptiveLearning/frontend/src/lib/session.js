import { apiFetch } from './api'
import { toast } from 'sonner'

/**
 * Close a practice session.
 *
 * Written twice before this — once in `Adaptive.jsx` as `finishSession` and
 * once in `Practice.jsx` as `endSession` — and the two copies had already
 * diverged on the part that matters: Adaptive reported a failure, Practice
 * swallowed it with a bare `catch {}`.
 *
 * Silence is the wrong half to have copied. The daily rollup and the chart
 * archive both run off this call, so a failed close is a session whose summary
 * is never written — and the raw rows it summarises are deleted at the end of
 * the school year, so the summary is what survives. The student meanwhile sees
 * the page reset and assumes it landed.
 *
 * It still never throws. A close that failed is tidied up by the stale-session
 * sweep the next time the student practises, so the page should carry on
 * resetting its own state either way; the caller decides what to do with the
 * page, this decides what the student is told.
 *
 * @param {string} id  session id; a falsy id is a no-op, not an error
 * @returns {Promise<boolean>} whether the backend confirmed the close
 */
/**
 * Record one answer against a session.
 *
 * The same story as `endSession` below, one endpoint over: both question pages
 * POST to `/api/sessions/{id}/answer` with the same three fields and both have
 * to say the same thing when it fails. They were written separately, and had
 * already diverged in the way that matters -- `Adaptive` reported the failure,
 * `Practice` had no `catch` at all, so a lost answer was an unhandled rejection
 * and the student was told nothing while the screen moved on.
 *
 * **Never throws**, which is what makes it safe to call without awaiting --
 * `Practice`'s countdown does exactly that from a timer callback, where there
 * is nothing to catch a rejection.
 *
 * Returns the backend's response so a caller that needs it can use it:
 * `Adaptive` reads `topic` off it to update its own tallies without guessing
 * which topic the question belonged to. `null` means the answer did not land.
 *
 * What it deliberately does *not* do is decide correctness. The two pages hold
 * questions in different shapes -- `options` against `answer_options` -- and
 * compare differently, so `correct` stays the caller's answer to give.
 *
 * @returns {Promise<object|null>} the response, or null if nothing was recorded
 */
export async function recordAnswer({ sessionId, questionId, selectedIndex, correct }) {
  // Loud rather than silent. An answer with no session or no question id cannot
  // be attributed to anything even if it were sent, and this is the failure the
  // whole call exists to stop being invisible.
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
