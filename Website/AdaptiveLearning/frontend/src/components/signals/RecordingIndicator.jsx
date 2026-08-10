/**
 * A passive statement that recording is happening. Nothing more.
 *
 * Consent living only in a settings tab means a student could be recorded for a
 * whole session with nothing on screen saying so, and settings pages are not
 * where people look. So the session header carries this.
 *
 * The constraints are what make it compatible with the decision that students
 * see no signal data about themselves:
 *
 *  - **No values.** It names the channels and stops. No numbers, no charts, no
 *    colour-coded status. It states *that* recording is happening, not *what*
 *    was recorded — and that distinction is the whole reason it can exist.
 *  - **Not interactive.** Not a button, no route to the data, no tooltip
 *    revealing a reading. A student who wants to change something goes to
 *    settings, which is where the decision lives.
 *  - **Silent when nothing is recording.** That is the default state for a
 *    student with no headband and no camera, and it must not put a permanent
 *    empty badge on their screen.
 *
 * It reflects **actual capture**, not consent. A consented channel whose sensor
 * dropped out has to stop showing: a chip claiming a recording that is not
 * happening is worse than no chip, and it is the same "cannot tell no-data from
 * zero" failure the reporting rules exist to prevent, pointed at the student.
 */

export default function RecordingIndicator({ channels }) {
  if (!channels?.length) return null

  return (
    <span
      // `status`, not `alert`: a screen reader should mention it when it
      // changes, not interrupt with it.
      role="status"
      className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full bg-slate-100 dark:bg-gray-800 text-[11px] font-bold text-gray-500 dark:text-gray-400">
      <span className="w-1.5 h-1.5 rounded-full bg-rose-500" aria-hidden="true" />
      Recording: {channels.join(' · ')}
    </span>
  )
}
