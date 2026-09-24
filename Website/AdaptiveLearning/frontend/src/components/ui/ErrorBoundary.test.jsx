import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

import ErrorBoundary from './ErrorBoundary'

// React logs caught errors itself; silenced so a passing run looks clean.
beforeEach(() => { vi.spyOn(console, 'error').mockImplementation(() => {}) })
afterEach(() => { vi.restoreAllMocks() })

function Boom() {
  throw new Error('render exploded')
}

describe('ErrorBoundary', () => {
  it('renders its children when nothing throws', () => {
    render(<ErrorBoundary resetKey="/a"><p>the page</p></ErrorBoundary>)

    expect(screen.getByText('the page')).toBeInTheDocument()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('shows the failure instead of unmounting the tree', () => {
    render(<ErrorBoundary resetKey="/a"><Boom /></ErrorBoundary>)

    expect(screen.getByRole('alert')).toBeInTheDocument()
    expect(screen.getByText(/something went wrong on this page/i)).toBeInTheDocument()
  })

  it('clears the error when the reset key changes', async () => {
    // A boundary latches, so it must reset on navigation.
    const { rerender } = render(
      <ErrorBoundary resetKey="/practice"><Boom /></ErrorBoundary>)
    expect(screen.getByRole('alert')).toBeInTheDocument()

    rerender(<ErrorBoundary resetKey="/dashboard"><p>the next page</p></ErrorBoundary>)

    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    expect(screen.getByText('the next page')).toBeInTheDocument()
  })

  it('does not clear the error while the reset key is unchanged', () => {
    // Re-rendering in place must not clear a real error.
    const { rerender } = render(
      <ErrorBoundary resetKey="/practice"><Boom /></ErrorBoundary>)

    rerender(<ErrorBoundary resetKey="/practice"><Boom /></ErrorBoundary>)

    expect(screen.getByRole('alert')).toBeInTheDocument()
  })

  it('retries in place when asked, without a reload', async () => {
    // Many render errors are transient; a click keeps in-memory state a reload loses.
    let shouldThrow = true
    const Flaky = () => {
      if (shouldThrow) throw new Error('once')
      return <p>recovered</p>
    }

    render(<ErrorBoundary resetKey="/a"><Flaky /></ErrorBoundary>)
    expect(screen.getByRole('alert')).toBeInTheDocument()

    shouldThrow = false
    await userEvent.click(screen.getByRole('button', { name: /try again/i }))

    expect(screen.getByText('recovered')).toBeInTheDocument()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })
})
