/** The app's loading placeholder. `height` is a Tailwind class, to match the real content. */
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

/** `count` placeholders in a column. */
export default function SkeletonList({ count = 3, height = 'h-16', gap = 'space-y-3', className = '' }) {
  return (
    <div className={gap}>
      {Array.from({ length: count }, (_, i) => (
        <Skeleton key={i} height={height} className={className} />
      ))}
    </div>
  )
}
