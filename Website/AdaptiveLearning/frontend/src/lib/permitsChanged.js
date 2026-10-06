// Tells this browser's lesson pages that a consent change landed, so they re-ask at once rather than at their poll.
const CHANNEL = 'recording-permits'

const open = () => (typeof BroadcastChannel === 'undefined' ? null : new BroadcastChannel(CHANNEL))

export function announcePermitsChanged() {
  const channel = open()
  if (!channel) return
  channel.postMessage('changed')
  channel.close()
}

/** Calls `onChange` for each announcement from another page; returns the unsubscribe. */
export function onPermitsChanged(onChange) {
  const channel = open()
  if (!channel) return () => {}
  channel.onmessage = () => onChange()
  return () => channel.close()
}
