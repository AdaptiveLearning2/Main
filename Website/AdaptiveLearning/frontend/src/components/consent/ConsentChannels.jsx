/**
 * The three consent switches, shared by student and parent surfaces.
 * The backend enforces who may do what; this only shows it via `role`.
 * Copy says "recorded", never "included"/"shown". Student copy is read by
 * children: short, concrete, and says what the sensor looks at.
 */

import { useState, useEffect, useCallback } from 'react'
import { apiFetch } from '../../lib/api'

/** Named for the sensor, not the signal derived from it. */
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
      erase: 'every focus and calm reading ever recorded from the headband',
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
      erase: 'every heart-rate reading ever recorded from the headband',
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
      erase: 'every reading ever recorded from the camera, including any heart rate it measured',
    },
  },
]

const NOTHING_CHANGED = 'Could not load these settings. Nothing has been changed.'

// A failed read after a 409: "nothing has changed" would be false.
const CONFLICT_UNREADABLE =
  'Someone else changed these settings, so your change was not applied. '
  + 'They could not be reloaded just now — please try again in a moment.'

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
  // Erasure state is separate from consent. `erasureAck` resets on open/close.
  const [erasureFor, setErasureFor] = useState(null)
  const [erasureAck, setErasureAck] = useState(false)
  const [erasing, setErasing] = useState(null)
  const [erasureNote, setErasureNote] = useState(null)

  // `conflict`: the message to keep after a 409-triggered reload.
  const load = useCallback((conflict = null) => {
    const failed = () => {
      setChannels(null)
      setError(conflict ? CONFLICT_UNREADABLE : NOTHING_CHANGED)
    }
    return apiFetch(`/api/consent/${studentId}`)
      .then(c => {
        // Fails closed as all-off; never render that as three off switches.
        if (c.retrieved === false) return failed()
        setChannels(c.channels)
        setError(conflict)
      })
      .catch(e => (conflict ? failed() : setError(String(e.message || e))))
  }, [studentId])

  useEffect(() => { load() }, [load])

  const commit = async (key, next) => {
    setSaving(key)
    setError(null)
    setErasureNote(null)
    try {
      const updated = await apiFetch(`/api/consent/${studentId}`, {
        method: 'PUT', body: { [key]: next },
      })
      setChannels(updated.channels)
    } catch (e) {
      // 409: the state moved under us. Reload, don't retry.
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

  const openErasure = (key) => {
    setErasureAck(false)      // must not carry over from an earlier panel
    setErasureNote(null)
    setErasureFor(key)
  }

  const closeErasure = () => {
    setErasureAck(false)
    setErasureFor(null)
  }

  const erase = async (key) => {
    setErasing(key)
    setError(null)
    setErasureNote(null)
    try {
      const out = await apiFetch(`/api/consent/${studentId}/erase`, {
        method: 'POST',
        // The channel name (`camera`), not the flag key, which 422s.
        body: { channel: key.replace('_enabled', ''), confirm: true },
      })
      // Reload so `erased_at` comes from the server.
      await load()
      setErasureNote(out.charts_failed
        ? {
            failed: true,
            text: 'The readings were erased. Some archived charts could not be '
              + 'removed and are no longer reachable from the app; please tell '
              + 'us so they can be cleared.',
          }
        : { failed: false, text: 'Erased.' })
      closeErasure()
    } catch (e) {
      setError(String(e.message || e))
    } finally {
      setErasing(null)
    }
  }

  const request = (key, next) => {
    // Only a student's switch-off is confirmed: they cannot undo it.
    if (role === 'student' && next === false) setConfirming(key)
    else commit(key, next)
  }

  if (error && !channels) {
    return <p className="text-sm text-rose-500">{error}</p>
  }
  if (!channels) {
    return <p className="text-sm text-gray-600 dark:text-gray-400">Loading…</p>
  }

  return (
    <div className="space-y-3">
      {error && <p className="text-sm text-rose-500">{error}</p>}
      {erasureNote && (
        <p className={`text-sm ${erasureNote.failed
          ? 'text-rose-600 dark:text-rose-400'
          : 'text-emerald-600 dark:text-emerald-400'}`}>
          {erasureNote.text}
        </p>
      )}

      {CHANNELS.map(ch => {
        const state = channels[ch.key.replace('_enabled', '')] || {}
        const copy  = role === 'student' ? ch.student : ch.parent
        const on    = !!state.enabled
        const since = formatDate(state.revoked_at)
        const erasedOn = formatDate(state.erased_at)
        // Disabled with a reason rather than hidden.
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

                  {/* Independent of `on`: a channel can be on with its past erased. */}
                  {erasedOn && (
                    <p className="text-xs font-bold text-rose-600 dark:text-rose-400 mt-2">
                      Readings recorded before {erasedOn} were erased
                      {role === 'student' ? ' at a parent\'s request' : ''}.
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

            {/* Parent only: the backend refuses a student. */}
            {role === 'parent' && erasureFor !== ch.key && (
              <button type="button"
                      onClick={() => openErasure(ch.key)}
                      className="mt-3 text-xs font-bold text-rose-600 dark:text-rose-400 hover:underline">
                Erase what this recorded
              </button>
            )}

            {role === 'parent' && erasureFor === ch.key && (
              <div className="mt-3 p-3 rounded-lg bg-rose-50 dark:bg-rose-950/40 border border-rose-200 dark:border-rose-800">
                <p className="text-xs font-bold text-rose-800 dark:text-rose-200">
                  Erase {copy.title.toLowerCase()} readings?
                </p>
                {/* Scope lives in the confirmation, not as standing copy. */}
                <p className="text-xs text-rose-700 dark:text-rose-300 mt-1">
                  This deletes {copy.erase}, the daily summaries built from it,
                  and the charts saved from those sessions. It cannot be undone.
                </p>
                <p className="text-xs text-rose-700 dark:text-rose-300 mt-1">
                  Practice sessions, answers and progress are kept
                  {ch.key === 'camera_enabled'
                    ? ', and readings from the headband are kept.'
                    : '.'}
                </p>
                {!on && (
                  <p className="text-xs text-rose-700 dark:text-rose-300 mt-1">
                    This does not change the setting above — it stays off.
                  </p>
                )}
                {on && (
                  <p className="text-xs text-rose-700 dark:text-rose-300 mt-1">
                    This setting stays on, so new readings will be recorded from
                    now on. Turn it off first if you do not want that.
                  </p>
                )}

                <label className="flex items-start gap-2 mt-3 cursor-pointer">
                  <input type="checkbox" checked={erasureAck}
                         onChange={e => setErasureAck(e.target.checked)}
                         className="mt-0.5" />
                  <span className="text-xs text-rose-800 dark:text-rose-200">
                    I understand this cannot be undone.
                  </span>
                </label>

                <div className="flex gap-2 mt-3">
                  <button onClick={() => erase(ch.key)}
                          disabled={!erasureAck || erasing === ch.key}
                          className="px-3 py-1.5 rounded-lg bg-rose-600 hover:bg-rose-700 text-white text-xs font-bold disabled:opacity-50 disabled:cursor-not-allowed">
                    {erasing === ch.key ? 'Erasing…' : 'Erase them'}
                  </button>
                  <button onClick={closeErasure}
                          disabled={erasing === ch.key}
                          className="px-3 py-1.5 rounded-lg bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 text-xs font-bold text-gray-700 dark:text-gray-200">
                    Cancel
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
