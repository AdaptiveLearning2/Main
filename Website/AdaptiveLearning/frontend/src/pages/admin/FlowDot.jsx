import { useEffect, useState } from 'react'
import useValueChange from '../../hooks/useValueChange'

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
  // *Which* timestamp is being pulsed for, rather than a boolean plus the
  // timestamp beside it. The two-state version had the ending effect depending
  // on `pulsedFor` without ever reading it -- correct, and load-bearing, and
  // invisible: an `exhaustive-deps` cleanup would have dropped it as unused and
  // silently taken the mid-pulse restart with it. One value the effect actually
  // reads cannot be pruned that way.
  const [pulseFor, setPulseFor] = useState(null)
  const pulse = pulseFor !== null

  // Started during render, not in an effect: it is derived entirely from a prop
  // changing, and doing it here means the dot lights on the same commit that
  // delivers the new timestamp.
  useValueChange(lastTs, next => { if (next) setPulseFor(next) })

  // Only *ending* it belongs in an effect, because a timer owns that. A
  // timestamp arriving mid-pulse changes `pulseFor`, so the effect re-runs and
  // the 600ms restarts rather than inheriting the remainder of the previous one.
  useEffect(() => {
    if (!pulseFor) return
    const t = setTimeout(() => setPulseFor(null), 600)
    return () => clearTimeout(t)
  }, [pulseFor])

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
