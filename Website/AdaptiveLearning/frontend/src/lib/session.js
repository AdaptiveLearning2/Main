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
