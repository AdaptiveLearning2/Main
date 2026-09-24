/** A question's Common Core code as a badge; renders nothing (never a placeholder) when absent. */
export default function CCSSBadge({ standard }) {
  if (typeof standard !== 'string' || !standard.trim()) return null
  return (
    <span
      className="inline-block text-[11px] font-bold tracking-wide px-2 py-0.5 rounded-md border border-gray-300 dark:border-gray-600 text-gray-600 dark:text-gray-400 mb-4"
      title="Common Core standard"
    >
      CCSS {standard}
    </span>
  )
}
