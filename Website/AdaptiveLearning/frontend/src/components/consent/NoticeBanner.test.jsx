/** The shared shell: the pending flag, and staying up when the acknowledgement fails. */
import { describe, it, expect, vi } from 'vitest'
import { act, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { Bell } from 'lucide-react'

import NoticeBanner from './NoticeBanner'

const draw = (props = {}) => render(
  <NoticeBanner tone="amber" icon={Bell} title="Something happened"
                onAcknowledge={vi.fn()} {...props}>
    <p>Body text.</p>
  </NoticeBanner>,
)

describe('NoticeBanner', () => {
  it('renders the title, the body and the action', () => {
    draw()
    expect(screen.getByText('Something happened')).toBeInTheDocument()
    expect(screen.getByText('Body text.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Got it' })).toBeInTheDocument()
  })

  it('stays up, and offers the button again, when the acknowledgement fails', async () => {
    // Dismissed on a failed write, the notice is never seen again.
    const onAcknowledge = vi.fn().mockRejectedValue(new Error('offline'))
    draw({ onAcknowledge })

    await userEvent.click(screen.getByRole('button'))

    await waitFor(() => expect(screen.getByRole('button')).toBeEnabled())
    expect(screen.getByText('Something happened')).toBeInTheDocument()
    expect(screen.getByRole('button')).toHaveTextContent('Got it')
  })

  it('handles the rejection rather than letting it escape the click', async () => {
    // The test above passes without the `catch` (the `finally` re-enables); this one does not.
    const seen = []
    const record = e => seen.push(e)
    globalThis.process.on('unhandledRejection', record)
    try {
      draw({ onAcknowledge: vi.fn().mockRejectedValue(new Error('offline')) })
      await userEvent.click(screen.getByRole('button'))
      // A macrotask: node reports unhandled only once the microtask queue drains.
      await new Promise(r => setTimeout(r, 0))
    } finally {
      globalThis.process.off('unhandledRejection', record)
    }
    expect(seen).toEqual([])
  })

  it('does not leave the button disabled after a successful acknowledgement', async () => {
    // A caller need not unmount the banner, so `busy` clears on success too.
    const onAcknowledge = vi.fn().mockResolvedValue(undefined)
    draw({ onAcknowledge })

    await userEvent.click(screen.getByRole('button'))

    expect(onAcknowledge).toHaveBeenCalledTimes(1)
    await waitFor(() => expect(screen.getByRole('button')).toBeEnabled())
  })

  it('disables the action while the acknowledgement is in flight', async () => {
    let release
    const onAcknowledge = vi.fn(() => new Promise(r => { release = r }))
    draw({ onAcknowledge })

    await userEvent.click(screen.getByRole('button'))

    await waitFor(() => expect(screen.getByRole('button')).toBeDisabled())
    expect(screen.getByRole('button')).toHaveTextContent('Saving…')

    // Inside act, or the re-render prints a spurious act() warning.
    await act(async () => { release() })
    expect(screen.getByRole('button')).toBeEnabled()
  })

  it.each(['amber', 'indigo', 'emerald'])('gives %s complete class names', tone => {
    // Catches a missing tone entry; cannot catch an interpolated class, which renders identically in jsdom.
    const { container } = render(
      <NoticeBanner tone={tone} icon={Bell} title="t" onAcknowledge={vi.fn()}>
        <p>b</p>
      </NoticeBanner>,
    )
    expect(container.firstChild.className).toContain(`bg-${tone}-50`)
    expect(container.firstChild.className).toContain(`border-${tone}-200`)
  })
})
