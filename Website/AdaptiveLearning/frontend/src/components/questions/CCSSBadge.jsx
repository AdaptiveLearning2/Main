/**
 * The Common Core code a question is scored against, as a small badge.
 *
 * An enrichment, like `QuestionFigure`: nothing rendered when the code is
 * absent, never a placeholder. A question written before the column existed
 * carries NULL, and "no standard" is not something to announce for a question
 * that has one.
 *
 * One style, no tone map: a standard is a citation, not a good or bad reading.
 */
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
