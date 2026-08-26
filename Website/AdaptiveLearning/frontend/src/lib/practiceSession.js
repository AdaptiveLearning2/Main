import { apiFetch } from './api'
import { toast } from 'sonner'

/**
 * `lib/session.js`'s counterparts, pointed at `/api/practice-sessions/*`
 * instead of `/api/sessions/*`. Kept as a separate file rather than
 * parameterizing `lib/session.js` with a base path -- that file's docstrings
 * and tests are explicitly pinned to the live-session routes, and threading a
 * path param through code with existing coverage buys nothing here.
 */

/** Record a graded (test-mode) answer. Never throws -- same reasoning as
 * `recordAnswer`: safe to call without awaiting, and a failure surfaces as a
 * toast rather than an unhandled rejection.
 *
 * @returns {Promise<object|null>} `{ok, topic}`, or null if nothing was recorded
 */
export async function recordPracticeAnswer({ sessionId, questionId, selectedIndex, correct }) {
  if (!sessionId || !questionId) {
    console.error('[practice answer] not recorded', { session: sessionId, question: questionId })
    toast.error('That answer could not be saved.')
    return null
  }
  try {
    return await apiFetch(`/api/practice-sessions/${sessionId}/answer`, {
      method: 'POST',
      body: { question_id: questionId, selected_index: selectedIndex, correct },
    })
  } catch (e) {
    console.error('[practice answer] not recorded', e)
    toast.error('That answer could not be saved.')
    return null
  }
}

/** Record an ungraded flashcard flip-to-reveal.
 *
 * Deliberately quiet on failure -- unlike a graded answer, a missed view only
 * costs one row in a progress summary nobody has looked at yet, not a score
 * a student is relying on. Logged, not toasted, so flipping cards stays fast.
 */
export async function markPracticeViewed({ sessionId, questionId }) {
  if (!sessionId || !questionId) return null
  try {
    return await apiFetch(`/api/practice-sessions/${sessionId}/view`, {
      method: 'POST',
      body: { question_id: questionId },
    })
  } catch (e) {
    console.error('[practice view] not recorded', e)
    return null
  }
}

/** Close a practice session, returning the closed row (with `topic_summary`)
 * so the results screen can render it -- unlike `endSession`, which only
 * needs to report success/failure to the live page calling it.
 *
 * @returns {Promise<object|null>} the closed session, or null on failure
 */
export async function endPracticeSession(id) {
  if (!id) return null
  try {
    return await apiFetch(`/api/practice-sessions/${id}/end`, { method: 'POST' })
  } catch (e) {
    console.error('[practice session] could not end', e)
    toast.error('Could not close that practice session cleanly.')
    return null
  }
}
