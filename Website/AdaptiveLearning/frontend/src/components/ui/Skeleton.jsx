/** Loading placeholders, in one shape.
 *
 * There were three looks in use at once -- bordered cards on most list pages,
 * flat grey bars on the student and teacher dashboards, and a slate variant in
 * SignalPanel -- so moving between pages during a load looked like moving
 * between applications. The bordered card was already the majority and is what
 * this standardises on; the outliers were converted rather than the other way
 * round, so the pages that already agreed were left untouched.
 *
 * `height` is a Tailwind class rather than a number because these have to line
 * up with the real content they stand in for, which differs per page: a
 * session row is not a stat card.
 */
export function Skeleton({ height = 'h-16', className = '' }) {
  return (
    <div
      // Announced as busy rather than as an empty region. A screen reader
      // otherwise reads a loading page as one with nothing on it.
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
