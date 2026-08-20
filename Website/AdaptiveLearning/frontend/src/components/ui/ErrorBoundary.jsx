import { Component } from 'react'

/** The last line between a thrown render and a white screen.
 *
 * With no error boundary, any component that threw during render unmounted
 * the whole application down to a blank document, error only in the console.
 *
 * Not `LoadError` -- that's for a failed request the page can re-issue. This
 * is for a bug: the page's own render threw.
 *
 * **A class, because there is no hook form of this.** `componentDidCatch`
 * and `getDerivedStateFromError` have no function-component equivalent.
 *
 * **`resetKey` makes it recoverable.** An error boundary latches -- once
 * `hasError` is true it stays true, so without this a crashed page would keep
 * showing the error screen on every later page. The layouts pass the current
 * pathname, so a navigation clears it. It's a prop rather than a `key` on the
 * element so the reset survives the layout being reordered around it.
 */
export default class ErrorBoundary extends Component {
  state = { error: null }

  static getDerivedStateFromError(error) {
    return { error }
  }

  componentDidCatch(error, info) {
    // No error-reporting service wired up, so the console is the whole record.
    console.error('[ErrorBoundary]', error, info?.componentStack)
  }

  componentDidUpdate(prev) {
    if (this.state.error && prev.resetKey !== this.props.resetKey) {
      this.setState({ error: null })
    }
  }

  render() {
    if (!this.state.error) return this.props.children

    return (
      <div className="p-6 lg:p-8">
        <div
          role="alert"
          className="max-w-lg mx-auto text-center bg-white dark:bg-gray-900 border border-gray-100 dark:border-gray-800 rounded-2xl p-8 shadow-sm"
        >
          <p className="text-4xl mb-3">😵</p>
          <h1 className="text-lg font-black text-gray-900 dark:text-white mb-1">
            Something went wrong on this page
          </h1>
          <p className="text-sm text-gray-500 dark:text-gray-400">
            {/* Not "your work was lost" -- answers are posted as given, so
                usually nothing was lost, and saying otherwise would scare a
                child out of a session that is fine. */}
            The rest of the app still works — try again, or move to another page.
          </p>

          {/* Message shown only in development -- in production it's a
              stack-shaped string a student can't act on. */}
          {import.meta.env.DEV && (
            <pre className="mt-4 text-left text-xs text-rose-600 dark:text-rose-400 whitespace-pre-wrap break-words">
              {String(this.state.error?.message || this.state.error)}
            </pre>
          )}

          <div className="mt-6 flex flex-wrap justify-center gap-2">
            <button
              onClick={() => this.setState({ error: null })}
              className="px-4 py-2 rounded-xl text-sm font-bold bg-indigo-600 hover:bg-indigo-700 text-white shadow transition"
            >
              Try again
            </button>
            <button
              onClick={() => window.location.reload()}
              className="px-4 py-2 rounded-xl text-sm font-bold bg-gray-100 dark:bg-gray-800 text-gray-700 dark:text-gray-300 hover:bg-gray-200 dark:hover:bg-gray-700 transition"
            >
              Reload the page
            </button>
          </div>
        </div>
      </div>
    )
  }
}
