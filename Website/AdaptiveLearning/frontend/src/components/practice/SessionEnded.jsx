/** A practice session the backend closed (409): retrying cannot reopen it, so offer a new one. */
export default function SessionEnded({ onRestart }) {
  return (
    <div className="text-center py-12" role="status">
      <p className="text-4xl mb-3">⏹️</p>
      <p className="text-gray-500 dark:text-gray-400">
        {/* No cause: the sweep, or a Finish in another tab, both answer 409. */}
        This practice session has already ended, so it can't take more answers.
      </p>
      {onRestart && (
        <button
          onClick={onRestart}
          className="mt-4 px-4 py-2 rounded-xl text-sm font-bold bg-indigo-600 text-white hover:bg-indigo-700 transition"
        >
          Start a new session
        </button>
      )}
    </div>
  )
}
