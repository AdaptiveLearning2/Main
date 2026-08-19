import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

vi.mock('../../lib/api', async () => await import('../../test/mocks/apiFetch'))

import { mockApi, resetApi } from '../../test/mocks/apiFetch'
import Questions from './Questions'

const QUESTION = {
  id: 'q-1',
  question_text: 'What is 7 x 8?',
  options: ['54', '56', '58'],
  correct_index: 1,
  subject: 'algebra',
  difficulty: 'easy',
}

beforeEach(() => {
  resetApi()
  mockApi({ '/api/questions?limit=1000': () => [QUESTION] })
})

async function openModal() {
  render(<Questions />)
  await userEvent.click(await screen.findByText('What is 7 x 8?'))
  return screen.getByRole('dialog')
}

describe('the question modal', () => {
  it('is a dialog, and names itself', async () => {
    // Without `role="dialog"` a screen reader announces a plain group and
    // gives no sign that the page behind is no longer the thing in front.
    const dialog = await openModal()

    expect(dialog).toHaveAttribute('aria-modal', 'true')
    expect(dialog).toHaveAccessibleName('What is 7 x 8?')
  })

  it('closes on Escape', async () => {
    // The backdrop click was the only way out, so a keyboard-only teacher
    // could open this and be stuck in it.
    await openModal()

    await userEvent.keyboard('{Escape}')

    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
  })

  it('keeps Tab inside the dialog', async () => {
    // Otherwise tabbing walks out into the page behind -- still there, still
    // scrollable, with nothing to say focus has left the thing on top.
    const dialog = await openModal()

    // Tab far enough to have escaped several times over had it not wrapped.
    for (let i = 0; i < 6; i += 1) await userEvent.tab()

    expect(dialog).toContainElement(document.activeElement)
  })

  it('gives focus back to what opened it', async () => {
    // Focus otherwise resets to the top of the document and the reader has to
    // find their place again.
    render(<Questions />)
    const row = await screen.findByText('What is 7 x 8?')
    const opener = row.closest('[role="button"], button, div')

    await userEvent.click(row)
    await screen.findByRole('dialog')
    await userEvent.keyboard('{Escape}')

    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    // Back inside the row that was clicked, rather than on document.body.
    expect(document.body).not.toBe(document.activeElement)
    expect(opener).toBeTruthy()
  })
})
