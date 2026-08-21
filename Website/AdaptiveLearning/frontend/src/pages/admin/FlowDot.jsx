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
  // Two states, and they are genuinely different questions:
  //
  //   pulsedFor   the newest timestamp we have *ever* lit for. Never cleared.
  //   pulsingFor  the one currently lit, or null. Cleared by the timer below.
  //
  // Collapsing them into one -- and driving it from a generic
  // previous-value hook -- looked like a simplification and was a bug. The
  // timer nulls the live one, so after a pulse ends there is nothing left
  // recording what was already shown: a timestamp that goes transiently
  // missing and comes back *unchanged* reads as a change, and the dot flashes
  // "fresh data" on an admin live monitor for data that is not new. A
  // previous-value hook cannot see that, because the value did change --
  // twice, back to where it started.
  //
  // So the comparison is against what was last *pulsed*, not against what was
  // last *rendered*, and only `pulsedFor` can answer that.
  const [pulsedFor, setPulsedFor] = useState(lastTs)
  const [pulsingFor, setPulsingFor] = useState(null)

  // Started during render, not in an effect, so the dot lights on the same
  // commit that delivers the new timestamp.
  if (lastTs && lastTs !== pulsedFor) {
    setPulsedFor(lastTs)
    setPulsingFor(lastTs)
  }

  // Ending the pulse needs a timer, so that part is an effect. It depends on
  // `pulsingFor`, so a timestamp arriving mid-pulse restarts the 600ms rather
  // than inheriting the remainder.
  useEffect(() => {
    if (!pulsingFor) return
    const t = setTimeout(() => setPulsingFor(null), 600)
    return () => clearTimeout(t)
  }, [pulsingFor])

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
        {pulsingFor !== null && flowing && (
          <span className="absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75 animate-ping" />
        )}
        <span className={`relative inline-flex rounded-full h-3 w-3 ${tone}`} />
      </span>
      <span className="text-xs font-semibold text-gray-600 dark:text-gray-400">{label}</span>
    </div>
  )
}
