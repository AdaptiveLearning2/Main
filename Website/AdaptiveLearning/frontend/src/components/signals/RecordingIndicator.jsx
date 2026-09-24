/**
 * A passive "Recording: <channels>" chip: no values, not interactive, hidden
 * when nothing records. Reflects actual capture, not consent.
 */

export default function RecordingIndicator({ channels }) {
  if (!channels?.length) return null

  return (
    <span
      // `status`, not `alert`: announced on change without interrupting.
      role="status"
      className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full bg-slate-100 dark:bg-gray-800 text-[11px] font-bold text-gray-500 dark:text-gray-400">
      <span className="w-1.5 h-1.5 rounded-full bg-rose-500" aria-hidden="true" />
      Recording: {channels.join(' · ')}
    </span>
  )
}
