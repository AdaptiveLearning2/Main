/**
 * The three consent switches, shared by the student and parent surfaces.
 *
 * One component because the *decision* is the same object on both sides —
 * `PUT /api/consent/{student_id}` — and two copies of that wiring is how the
 * two surfaces end up disagreeing about what a channel means.
 *
 * What differs is who may do what, and that is the backend's rule, not this
 * component's: a student may only move a flag **true → false**, and only a
 * linked parent may move it back. This renders that asymmetry (`role`) so the
 * student is told before they commit, but it does not enforce it — a UI that
 * enforces a permission is a UI that can be wrong about one.
 *
 * Copy rules this has to hold to, from the plan:
 *  - Say **recorded**, never *included* or *shown*. The old control was a
 *    display filter and anyone who saw it will read the old vocabulary that way.
 *  - No disclaimer sentence about what the control *doesn't* do. Needing one is
 *    the signal the control is wrong.
 *  - The student strings are read by children, some with learning
 *    disabilities: short sentences, concrete verbs, and no "facial
 *    recognition" — say what it looks at and what it works out.
 */

import { useState, useEffect, useCallback } from 'react'
import { apiFetch } from '../../lib/api'

/**
 * Named for the **sensor**, not the signal derived from it.
 * `headband_optical` is heart rate today; the Athena's OPTICS packet also
 * carries fNIRS, so a `_ppg_` name would go stale. The label can change without
 * the column changing.
 */
export const CHANNELS = [
  {
    key: 'eeg_enabled',
    icon: '🧠',
    student: {
      title: 'Headband',
      body: 'Your headband measures how focused and how calm you are while you practise. Turn this off and it won\'t be measured or saved.',
    },
    parent: {
      title: 'Headband (EEG)',
      body: 'Records focus and calm during practice sessions. Off means it is never measured or saved — not hidden from reports.',
    },
  },
  {
    key: 'headband_optical_enabled',
    icon: '💓',
    student: {
      title: 'Heart sensor',
      body: 'Your headband can also measure your heart rate while you practise. Turn this off and it won\'t be measured or saved.',
    },
    parent: {
      title: 'Heart sensor (headband)',
      body: 'Records heart rate and heart-rate variability during practice sessions. Off means it is never measured or saved — not hidden from reports.',
    },
  },
  {
    key: 'camera_enabled',
    icon: '📷',
    student: {
      title: 'Camera',
      body: 'The camera looks at your face to work out how you\'re finding the questions. No video is ever saved, and nobody watches it. Turn this off and the camera won\'t be used.',
    },
    parent: {
      title: 'Camera',
      body: 'Records how the child appears to be finding the questions, from the camera on their device. No video is stored — only the reading. Off means it is never measured or saved.',
    },
  },
]

function formatDate(iso) {
  if (!iso) return null
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? null : d.toLocaleDateString(undefined, {
    day: 'numeric', month: 'long', year: 'numeric',
  })
}

function Switch({ on, disabled, onChange, label }) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={on}
      aria-label={label}
      disabled={disabled}
      onClick={() => onChange(!on)}
      className={`relative flex-shrink-0 w-12 h-7 rounded-full transition disabled:opacity-40 disabled:cursor-not-allowed ${
        on ? 'bg-emerald-500' : 'bg-gray-300 dark:bg-gray-600'
      }`}>
      <span className={`absolute top-1 w-5 h-5 bg-white rounded-full shadow transition-all ${
        on ? 'left-6' : 'left-1'
      }`} />
    </button>
  )
}

export default function ConsentChannels({ studentId, role, studentName = null }) {
  const [channels, setChannels] = useState(null)
  const [error, setError]       = useState(null)
  const [saving, setSaving]     = useState(null)
  const [confirming, setConfirming] = useState(null)

  // Returns its promise, and takes the message to leave in place. A reload
  // triggered by a conflict would otherwise clear the very error that caused
  // it -- the read succeeds, so `setError(null)` fires and the parent is told
  // nothing went wrong while their change silently did not apply.
  const load = useCallback((keepError = null) => {
    return apiFetch(`/api/consent/${studentId}`)
      .then(c => {
        // `retrieved: false` means the read itself failed. Rendering that as
        // "everything is off" would tell a parent their child's channels are
        // disabled when we simply could not find out — the same three-state
        // distinction the reporting surfaces keep.
        //
        // Which is why the banner alone was not enough. `_consent()` fails
        // **closed**, so a failed read does not arrive as an error shape or a
        // partial one — it is a complete, plausible payload with all three
        // channels `enabled: false` and no `revoked_at` on any of them. Setting
        // it drew the banner *and* three off switches; for `role="student"`,
        // three locked ones reading "A parent can turn this back on for you",
        // which is a claim about a decision nobody made, during what may be a
        // few seconds of outage. Holding `channels` at null is what makes the
        // early return below the entire render.
        const failed = c.retrieved === false
        setChannels(failed ? null : c.channels)
        setError(failed
          ? 'Could not load these settings. Nothing has been changed.'
          : keepError)
      })
      .catch(e => setError(String(e.message || e)))
  }, [studentId])

  useEffect(() => { load() }, [load])

  const commit = async (key, next) => {
    setSaving(key)
    setError(null)
    try {
      const updated = await apiFetch(`/api/consent/${studentId}`, {
        method: 'PUT', body: { [key]: next },
      })
      setChannels(updated.channels)
    } catch (e) {
      // 409 is its own case: the decision moved under us — a student withdrew
      // while a parent was re-enabling, or the reverse. Losing that silently
      // is how someone records against a refusal, so it reloads rather than
      // retrying.
      if (e.status === 409) {
        await load('This was changed somewhere else. Reloaded — please check and try again.')
      } else {
        setError(String(e.message || e))
      }
    } finally {
      setSaving(null)
      setConfirming(null)
    }
  }

  const request = (key, next) => {
    // Only the student's own switch-off needs confirming: it is the one action
    // this UI cannot undo for them. A parent's change is reversible by the
    // same parent on the same screen.
    if (role === 'student' && next === false) setConfirming(key)
    else commit(key, next)
  }

  if (error && !channels) {
    return <p className="text-sm text-rose-500">{error}</p>
  }
  if (!channels) {
    return <p className="text-sm text-gray-400">Loading…</p>
  }

  return (
    <div className="space-y-3">
      {error && <p className="text-sm text-rose-500">{error}</p>}

      {CHANNELS.map(ch => {
        const state = channels[ch.key.replace('_enabled', '')] || {}
        const copy  = role === 'student' ? ch.student : ch.parent
        const on    = !!state.enabled
        const since = formatDate(state.revoked_at)
        // The student cannot re-enable. Shown as a disabled switch with the
        // reason beside it rather than a hidden control: a switch that vanishes
        // when you turn it off looks like a bug.
        const locked = role === 'student' && !on

        return (
          <div key={ch.key}
               className="p-4 bg-slate-50 dark:bg-gray-800 rounded-xl border border-gray-100 dark:border-gray-700">
            <div className="flex items-start justify-between gap-4">
              <div className="flex items-start gap-3 min-w-0">
                <span className="text-2xl" aria-hidden="true">{ch.icon}</span>
                <div className="min-w-0">
                  <p className="font-bold text-gray-900 dark:text-white text-sm">{copy.title}</p>
                  <p className="text-xs text-gray-500 dark:text-gray-400 mt-0.5">{copy.body}</p>

                  {!on && since && (
                    <p className="text-xs font-bold text-amber-600 dark:text-amber-400 mt-2">
                      Not recorded — turned off on {since}
                      {state.revoked_by === 'parent' && role === 'student' && ' by a parent'}
                      {state.revoked_by === 'student' && role === 'parent' &&
                        ` by ${studentName || 'your child'}`}
                    </p>
                  )}
                  {locked && (
                    <p className="text-xs text-gray-500 dark:text-gray-400 mt-1">
                      A parent can turn this back on for you.
                    </p>
                  )}
                </div>
              </div>

              <Switch on={on} disabled={locked || saving === ch.key}
                      label={copy.title}
                      onChange={next => request(ch.key, next)} />
            </div>

            {confirming === ch.key && (
              <div className="mt-3 p-3 rounded-lg bg-amber-50 dark:bg-amber-950/40 border border-amber-200 dark:border-amber-800">
                <p className="text-xs font-bold text-amber-800 dark:text-amber-200">
                  Turn off {copy.title.toLowerCase()}?
                </p>
                <p className="text-xs text-amber-700 dark:text-amber-300 mt-1">
                  This turns off straight away and stays off. If you want it back
                  on later, a parent has to switch it back on for you.
                </p>
                <div className="flex gap-2 mt-3">
                  <button onClick={() => commit(ch.key, false)}
                          disabled={saving === ch.key}
                          className="px-3 py-1.5 rounded-lg bg-amber-600 hover:bg-amber-700 text-white text-xs font-bold disabled:opacity-50">
                    {saving === ch.key ? 'Turning off…' : 'Turn it off'}
                  </button>
                  <button onClick={() => setConfirming(null)}
                          className="px-3 py-1.5 rounded-lg bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 text-xs font-bold text-gray-700 dark:text-gray-200">
                    Keep it on
                  </button>
                </div>
              </div>
            )}
          </div>
        )
      })}
    </div>
  )
}
