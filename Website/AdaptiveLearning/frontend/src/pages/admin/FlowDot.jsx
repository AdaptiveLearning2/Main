import { useEffect, useState } from 'react'

/**
 * A channel's liveness as a light: flowing (green, pulses per new sample),
 * stale (amber), seen (slate: reported, not recently), never (hollow).
 * Pulses only when the timestamp changes, so a stopped sensor goes still.
 */
export default function FlowDot({ channel, label }) {
  const { flowing, stale, seen, last_ts: lastTs } = channel || {}
  // pulsedFor: newest timestamp ever lit, never cleared. pulsingFor: the one lit
  // now. Compare against pulsedFor, or a timestamp that blinks out and back re-pulses.
  const [pulsedFor, setPulsedFor] = useState(lastTs)
  const [pulsingFor, setPulsingFor] = useState(null)

  // During render, so the dot lights on the commit that delivers the timestamp.
  if (lastTs && lastTs !== pulsedFor) {
    setPulsedFor(lastTs)
    setPulsingFor(lastTs)
  }

  // Keyed on `pulsingFor`, so a mid-pulse timestamp restarts the 600ms.
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
