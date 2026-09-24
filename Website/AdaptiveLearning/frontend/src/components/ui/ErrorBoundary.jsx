import { Component } from 'react'

/**
 * Catches a thrown render so the app doesn't go blank (a class: no hook form exists).
 * `resetKey` (the layouts pass the pathname) clears the latched error on navigation.
 */
export default class ErrorBoundary extends Component {
  state = { error: null }

  static getDerivedStateFromError(error) {
    return { error }
  }

  componentDidCatch(error, info) {
    // No error-reporting service; the console is the only record.
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
            {/* Not "your work was lost": answers are posted as given. */}
            The rest of the app still works — try again, or move to another page.
          </p>

          {/* Development only. */}
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
