import { useEffect, useState } from 'react'

/**
 * A channel's liveness as a light, not a number.
 *
 * Four states, and they are not a scale -- "never reported" is not a worse
 * version of "stale", it is a different fact (a session that never had this
 * sensor, versus one whose sensor stopped). Rendering them on one dimension is
 * what makes a blank tile mean two things at once, which the reporting rules
 * elsewhere in this app exist to prevent.
 *
 *   flowing  green, pulsing on each new sample
 *   stale    amber, steady
 *   seen     slate, steady        (reported once, not recently, not yet stale)
 *   never    hollow outline
 *
 * The pulse fires on a *changed* timestamp rather than on every poll, so a
 * sensor that stopped goes visibly still instead of blinking at the poll rate
 * -- which would read as healthy.
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
  const pulse = pulsingFor !== null

  // Started during render, not in an effect: it is derived entirely from a prop
  // changing, and doing it here means the dot lights on the same commit that
  // delivers the new timestamp.
  if (lastTs && lastTs !== pulsedFor) {
    setPulsedFor(lastTs)
    setPulsingFor(lastTs)
  }

  // Only *ending* it belongs in an effect, because a timer owns that. It reads
  // `pulsingFor` and depends on `pulsingFor`, so a timestamp arriving mid-pulse
  // restarts the 600ms rather than inheriting the remainder -- and no dependency
  // here is load-bearing without being read, which is what the earlier
  // `[pulse, pulsedFor]` pair got wrong.
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
        {pulse && flowing && (
          <span className="absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75 animate-ping" />
        )}
        <span className={`relative inline-flex rounded-full h-3 w-3 ${tone}`} />
      </span>
      <span className="text-xs font-semibold text-gray-600 dark:text-gray-400">{label}</span>
    </div>
  )
}
