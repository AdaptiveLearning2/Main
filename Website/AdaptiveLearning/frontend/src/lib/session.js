import { apiFetch } from './api'
import { toast } from 'sonner'

/**
 * Record one answer against a session. Never throws (safe from a timer callback).
 * @returns {Promise<object|null>} the response (`topic` included); `{ ended: true }`, untoasted,
 *   when the session was closed server-side; or null if not recorded
 */
export async function recordAnswer({ sessionId, questionId, selectedIndex, correct }) {
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
    // 409: closed under the page (the sweep, the live monitor, another tab); the caller retries.
    if (e?.status === 409) return { ended: true }
    console.error('[answer] not recorded', e)
    toast.error('That answer could not be saved.')
    return null
  }
}

/**
 * Push only: tell the backend a headband is streaming for this session (its alerts need it).
 * Never throws, and silent: the student can do nothing about a failure, and recording goes on.
 * @returns {Promise<boolean>} whether the backend recorded it
 */
export async function markEegStarted(sessionId) {
  if (!sessionId) return false
  try {
    await apiFetch(`/api/sessions/${sessionId}/eeg-started`, { method: 'POST' })
    return true
  } catch (e) {
    console.error('[session] could not report the EEG start', e)
    return false
  }
}

/**
 * Close a practice session. Never throws; tells the student if it failed.
 * @returns {Promise<boolean>} whether the backend confirmed the close (falsy id: false)
 */
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

/**
 * The student's own sessions, newest first: `{ sessions, total, truncated }`, or a rejection.
 * Any other body shape is a failed read, not "no sessions"; `total`/`truncated` are null when the backend could not count.
 * @param {{ limit?: number }} [opts]  ask for fewer rows; the backend clamps
 * @returns {Promise<{ sessions: object[], total: number|null, truncated: boolean|null }>}
 */
export async function fetchSessionList({ limit } = {}) {
  const r = await apiFetch(limit ? `/api/sessions?limit=${limit}` : '/api/sessions')
  if (!r || typeof r !== 'object' || !Array.isArray(r.sessions)) {
    throw new Error('The session list came back in a shape this page does not read')
  }
  return {
    sessions:  r.sessions,
    total:     typeof r.total === 'number' ? r.total : null,
    truncated: typeof r.truncated === 'boolean' ? r.truncated : null,
  }
}
