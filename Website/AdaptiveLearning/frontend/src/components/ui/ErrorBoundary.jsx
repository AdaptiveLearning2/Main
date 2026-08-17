import { Component } from 'react'

/** The last line between a thrown render and a white screen.
 *
 * There was no error boundary anywhere in this app, so any component that threw
 * during render unmounted the entire tree -- not the page, the *application*,
 * down to a blank document with the error only in the console. `Practice.jsx`
 * mapping an absent `options` array was one reachable path to it; the reason to
 * have this is that there is no way to enumerate the rest.
 *
 * Deliberately not `LoadError`. That one is for a *failed request*, which the
 * page knows about and can re-issue. This is for a bug -- the page's own render
 * threw, and the honest thing to say is that something went wrong rather than
 * to blame the backend.
 *
 * **A class, because there is no hook form of this.** `componentDidCatch` and
 * `getDerivedStateFromError` have no function-component equivalent in React 19;
 * this is the one place in the codebase that has to be a class.
 *
 * **`resetKey` is what makes it recoverable.** An error boundary latches: once
 * `hasError` is true it stays true for the life of the component, so without
 * this a student who crashed the practice page would keep seeing the error
 * screen on every page they navigated to afterwards, having done nothing wrong
 * and with no way out but a reload. The layouts pass the current pathname, so a
 * navigation clears it. It is a prop rather than a `key` on the element so the
 * reset survives someone reordering the layout around it -- coupling this to
 * where it happens to sit in the tree is how it would quietly stop working.
 */
export default class ErrorBoundary extends Component {
  state = { error: null }

  static getDerivedStateFromError(error) {
    return { error }
  }

  componentDidCatch(error, info) {
    // The only place this surfaces otherwise. There is no error-reporting
    // service wired up here, so the console is the whole of the record.
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
            {/* Not "your work was lost". Answers are posted as they are given,
                so in the common case nothing was, and saying otherwise would
                frighten a child out of a session that is fine. */}
            The rest of the app still works — try again, or move to another page.
          </p>

          {/* The message itself only in development. In production it is a
              stack-shaped string a student cannot act on, and it can carry
              internals into a screenshot. */}
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
