/**
 * "This didn't load": never drawn as an empty list. `error.status` picks the sentence:
 * 403 refused and 404 not found (neither retried), 401 session expired, 429 too many requests,
 * 503 a read that failed just now, anything else (no status included) could not reach the backend.
 */
export default function LoadError({ what = 'this page', onRetry, error }) {
  const status = error?.status

  // No retry under a refusal or a not-found: asking again cannot change the answer.
  const retryable = status !== 403 && status !== 404

  const message =
    status === 403 ? `You don't have access to ${what}.`
    : status === 404 ? `Couldn't find ${what}.`
    : status === 401 ? `Your session has expired. Sign in again to see ${what}.`
    : status === 429 ? `Too many requests just now, so ${what} didn't load. Wait a moment and try again.`
    // The backend answered but a read behind it failed; apiFetch first retries one sent with Retry-After.
    : status === 503 ? `Couldn't load ${what} just now. Try again in a moment.`
    : `Couldn't load ${what}. Make sure the backend is running.`

  return (
    <div className="text-center py-12" role="status">
      <p className="text-4xl mb-3">⚠️</p>
      <p className="text-gray-500 dark:text-gray-400">{message}</p>
      {onRetry && retryable && (
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
