/**
 * The shell's own behaviour, tested once instead of three times.
 *
 * The three notices keep their own tests for what they *say* and which
 * endpoint they call. What lives here is what they used to each restate: the
 * pending flag, and leaving the banner standing when the acknowledgement does
 * not land.
 */
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
    // The reason this is a component rather than a copied div. A notice that
    // dismisses itself on a failed write is one the person never sees again --
    // they have not actually been told.
    const onAcknowledge = vi.fn().mockRejectedValue(new Error('offline'))
    draw({ onAcknowledge })

    await userEvent.click(screen.getByRole('button'))

    await waitFor(() => expect(screen.getByRole('button')).toBeEnabled())
    expect(screen.getByText('Something happened')).toBeInTheDocument()
    expect(screen.getByRole('button')).toHaveTextContent('Got it')
  })

  it('handles the rejection rather than letting it escape the click', async () => {
    // The half the test above does *not* prove: with the `catch` removed, the
    // banner still stands and the button still re-enables, because the
    // `finally` does that either way. What changes is that the rejection
    // escapes an event handler nothing awaits, and an unhandled rejection is
    // a crash report for a case that was handled on purpose.
    const seen = []
    const record = e => seen.push(e)
    globalThis.process.on('unhandledRejection', record)
    try {
      draw({ onAcknowledge: vi.fn().mockRejectedValue(new Error('offline')) })
      await userEvent.click(screen.getByRole('button'))
      // A macrotask, not a microtask: node only reports a rejection unhandled
      // once the microtask queue that could still have attached a handler has
      // drained.
      await new Promise(r => setTimeout(r, 0))
    } finally {
      globalThis.process.off('unhandledRejection', record)
    }
    expect(seen).toEqual([])
  })

  it('does not leave the button disabled after a successful acknowledgement', async () => {
    // `onAcknowledge` normally clears whatever made the banner render, so this
    // unmounts -- but nothing forces it to, and a shell that only cleared the
    // flag on the failure path would leave a permanently dead button behind
    // for any caller that did not.
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

    // Inside act, or the re-render this resolve triggers happens outside
    // React's control and the warning it prints is indistinguishable from a
    // real one in the next person's CI log.
    await act(async () => { release() })
    expect(screen.getByRole('button')).toBeEnabled()
  })

  it.each(['amber', 'indigo', 'emerald'])('gives %s complete class names', tone => {
    // This catches a tone whose entry is missing or misnamed -- the map is the
    // only thing standing between `tone="emerald"` and a crash on `t.box`.
    //
    // It deliberately does NOT catch the Tailwind problem the component's own
    // comment describes: an interpolated `bg-${tone}-50` renders the identical
    // class string, so the DOM is the same and only the generated stylesheet
    // differs. Nothing in jsdom can see that. Reviewing the source is the
    // check there; this is not it.
    const { container } = render(
      <NoticeBanner tone={tone} icon={Bell} title="t" onAcknowledge={vi.fn()}>
        <p>b</p>
      </NoticeBanner>,
    )
    expect(container.firstChild.className).toContain(`bg-${tone}-50`)
    expect(container.firstChild.className).toContain(`border-${tone}-200`)
  })
})
