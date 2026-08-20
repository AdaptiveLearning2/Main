/** Loading placeholders, in one shape.
 *
 * Three looks used to be in use at once, so moving between pages during a
 * load looked like moving between applications. Standardised on the
 * bordered card, since it was already the majority.
 *
 * `height` is a Tailwind class rather than a number, since these have to
 * line up with the real content they stand in for -- a session row is not
 * a stat card.
 */
export function Skeleton({ height = 'h-16', className = '' }) {
  return (
    <div
      // Announced as busy, or a screen reader reads a loading page as empty.
      role="status"
      aria-label="Loading"
      className={`${height} bg-white dark:bg-gray-900 rounded-2xl border border-gray-100 dark:border-gray-800 animate-pulse ${className}`}
    />
  )
}

/** `count` placeholders in a column. The common case by far. */
export default function SkeletonList({ count = 3, height = 'h-16', gap = 'space-y-3', className = '' }) {
  return (
    <div className={gap}>
      {Array.from({ length: count }, (_, i) => (
        <Skeleton key={i} height={height} className={className} />
      ))}
    </div>
  )
}
