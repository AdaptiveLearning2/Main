/**
 * A passive statement that recording is happening. Nothing more.
 *
 * Consent living only in a settings tab meant a student could be recorded a
 * whole session with nothing on screen saying so. This is what the session
 * header carries instead:
 *
 *  - **No values.** Names the channels and stops -- states *that* recording
 *    is happening, not *what* was recorded, which is what keeps it compatible
 *    with students seeing no signal data about themselves.
 *  - **Not interactive.** No button, no route to the data, no tooltip. To
 *    change something a student goes to settings.
 *  - **Silent when nothing is recording**, rather than a permanent empty badge.
 *
 * Reflects **actual capture**, not consent -- a consented channel whose
 * sensor dropped out must stop showing, or the chip claims a recording that
 * isn't happening.
 */

export default function RecordingIndicator({ channels }) {
  if (!channels?.length) return null

  return (
    <span
      // `status`, not `alert`: a screen reader mentions it on change rather
      // than interrupting.
      role="status"
      className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full bg-slate-100 dark:bg-gray-800 text-[11px] font-bold text-gray-500 dark:text-gray-400">
      <span className="w-1.5 h-1.5 rounded-full bg-rose-500" aria-hidden="true" />
      Recording: {channels.join(' · ')}
    </span>
  )
}
