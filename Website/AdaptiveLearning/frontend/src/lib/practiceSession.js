import { apiFetch } from './api'
import { toast } from 'sonner'

/** `lib/session.js`'s counterparts for `/api/practice-sessions/*`. */

/**
 * Record a graded (test-mode) answer. Never throws; failure is a toast.
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

/** Record an ungraded flashcard reveal. Failure is logged, not toasted. */
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

/**
 * Close a practice session, returning the closed row (with `topic_summary`).
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
