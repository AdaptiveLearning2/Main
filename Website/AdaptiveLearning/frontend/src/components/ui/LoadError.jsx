/** "This didn't load" — the state that must never be drawn as an empty list.
 *
 * A failed request used to set `loading` false and leave the rows empty, so
 * the page said "no sessions yet" for a backend that was simply unreachable.
 *
 * Not a toast -- a toast disappears while the condition doesn't, and the
 * empty state it needs to override is still on screen afterwards.
 *
 * `what` names the thing, so the sentence reads as a fact about this page
 * rather than a generic apology.
 */
export default function LoadError({ what = 'this page', onRetry }) {
  return (
    <div className="text-center py-12" role="status">
      <p className="text-4xl mb-3">⚠️</p>
      <p className="text-gray-500 dark:text-gray-400">
        Couldn&apos;t load {what}. Make sure the backend is running.
      </p>
      {onRetry && (
        <button
          onClick={onRetry}
          className="mt-4 px-4 py-2 rounded-xl text-sm font-bold bg-gray-100 dark:bg-gray-800 text-gray-700 dark:text-gray-300 hover:bg-gray-200 dark:hover:bg-gray-700 transition"
        >
          Try again
        </button>
      )}
    </div>
  )
}
