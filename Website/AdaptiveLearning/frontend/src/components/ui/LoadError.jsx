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
 *
 * `error` is optional and picks the sentence. Without it every failure said
 * "make sure the backend is running", which is a claim about the server, and
 * it is wrong for the case where the server answered perfectly well and said
 * no: a teacher hit exactly that on the Question Bank when the student filter
 * sent an email to an endpoint that resolves a uuid, and went to check
 * whether the backend was up. It was. Sending someone to inspect the wrong
 * layer is its own cost, and this codebase has paid it more than once.
 *
 * Three states, not two, and the third is the one that gets collapsed:
 *
 *   403        the server understood and refused. Not retryable, ever.
 *   401        we are not signed in, or no longer are. A retry can work,
 *              once the session is back.
 *   anything   including an error carrying no `status` at all -- a dropped
 *   else       connection, a DNS failure, an aborted fetch. That genuinely is
 *              "could not reach the backend", so it keeps the original
 *              sentence. Relabelling it as a permissions problem would be the
 *              same mistake pointing the other way.
 *
 * `apiFetch` attaches `.status` to the Error it throws for a non-2xx, which
 * is what makes this possible; a caller with no error to hand passes none and
 * gets today's wording.
 */
export default function LoadError({ what = 'this page', onRetry, error }) {
  const status = error?.status

  // A retry button under a refusal invites someone to click a thing that
  // cannot work. Withholding it is part of the message.
  const retryable = status !== 403

  const message =
    status === 403 ? `You don't have access to ${what}.`
    : status === 401 ? `Your session has expired. Sign in again to see ${what}.`
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
