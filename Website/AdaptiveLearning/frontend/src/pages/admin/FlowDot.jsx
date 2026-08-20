import { useEffect, useState } from 'react'

/**
 * Shows a channel's liveness as a light, not a number.
 *
 * Four states, not a scale: "never reported" (no sensor) and "stale" (sensor
 * stopped) are different facts, not different severities of the same thing.
 *
 *   flowing  green, pulsing on each new sample
 *   stale    amber, steady
 *   seen     slate, steady        (reported once, not recently, not yet stale)
 *   never    hollow outline
 *
 * The pulse fires only when the timestamp changes, not on every poll, so a
 * stopped sensor goes still instead of blinking as if it were healthy.
 */
export default function FlowDot({ channel, label }) {
  const { flowing, stale, seen, last_ts: lastTs } = channel || {}
  const [pulse, setPulse] = useState(false)
  const [pulsedFor, setPulsedFor] = useState(lastTs)

  // Start the pulse during render, not in an effect, so the dot lights on
  // the same commit that delivers the new timestamp.
  if (lastTs && lastTs !== pulsedFor) {
    setPulsedFor(lastTs)
    setPulse(true)
  }

  // Ending the pulse needs a timer, so that part is an effect. Keyed on
  // `pulsedFor` too, so a new timestamp mid-pulse restarts the full 600ms.
  useEffect(() => {
    if (!pulse) return
    const t = setTimeout(() => setPulse(false), 600)
    return () => clearTimeout(t)
  }, [pulse, pulsedFor])

  let tone = 'border-2 border-gray-300 dark:border-gray-600 bg-transparent'
  let title = `${label}: no data has ever arrived for this session`
  if (seen && flowing) {
    tone = 'bg-emerald-500'
    title = `${label}: receiving data`
  } else if (seen && stale) {
    tone = 'bg-amber-500'
    title = `${label}: nothing for over 10 minutes`
  } else if (seen) {
    tone = 'bg-slate-400'
    title = `${label}: last data over 90 seconds ago`
  }

  return (
    <div className="flex items-center gap-2" title={title}>
      <span className="relative flex h-3 w-3 items-center justify-center">
        {pulse && flowing && (
          <span className="absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75 animate-ping" />
        )}
        <span className={`relative inline-flex rounded-full h-3 w-3 ${tone}`} />
      </span>
      <span className="text-xs font-semibold text-gray-600 dark:text-gray-400">{label}</span>
    </div>
  )
}
