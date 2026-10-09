import LoadError from '../../components/ui/LoadError'

export function Tile({ label, value, hint }) {
  return (
    <div className="bg-white dark:bg-gray-900 border border-gray-100 dark:border-gray-800 rounded-xl px-4 py-3">
      <p className="text-2xl font-black text-gray-900 dark:text-white">{value}</p>
      <p className="text-xs text-gray-600 dark:text-gray-400 font-semibold">{label}</p>
      {hint && <p className="mt-0.5 text-xs text-gray-600 dark:text-gray-400">{hint}</p>}
    </div>
  )
}

// The request worked and the read inside it did not: never drawn as "nothing happened".
export function Unread({ what, note = 'This is not the same as there being none.' }) {
  return (
    <div className="rounded-xl border border-amber-200 bg-amber-50 p-4 dark:border-amber-900 dark:bg-amber-950/40">
      <p className="font-bold text-amber-900 dark:text-amber-200">{what} could not be read.</p>
      <p className="mt-1 text-sm text-amber-800 dark:text-amber-300">{note}</p>
    </div>
  )
}

// Before the first read, a failure is the whole card; after it, the last data stays with a note.
export function ReadState({ res, what, children }) {
  const { data, loadError } = res
  if (!data) {
    return loadError
      ? <LoadError error={loadError} what={what} />
      : <p className="text-sm text-gray-600 dark:text-gray-400">Loading…</p>
  }
  return (
    <>
      {loadError && (
        <p className="text-xs text-amber-800 dark:text-amber-300">
          The last refresh failed{loadError.status ? ` (${loadError.status})` : ''}; this is what was read before it.
        </p>
      )}
      {children(data)}
    </>
  )
}
