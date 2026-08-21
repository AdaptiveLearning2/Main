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
    // role="dialog" is what tells a screen reader the page behind is no longer in front.
    const dialog = await openModal()

    expect(dialog).toHaveAttribute('aria-modal', 'true')
    expect(dialog).toHaveAccessibleName('What is 7 x 8?')
  })

  it('closes on Escape', async () => {
    // Without this, a keyboard-only user has no way to close the modal.
    await openModal()

    await userEvent.keyboard('{Escape}')

    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
  })

  it('keeps Tab inside the dialog', async () => {
    // Otherwise tabbing walks out into the page behind it.
    const dialog = await openModal()

    // Enough tabs to have escaped several times over if it didn't wrap.
    for (let i = 0; i < 6; i += 1) await userEvent.tab()

    expect(dialog).toContainElement(document.activeElement)
  })

  it('gives focus back to what opened it', async () => {
    // Otherwise focus resets to the top of the document.
    render(<Questions />)
    const row = await screen.findByText('What is 7 x 8?')
    const opener = row.closest('[role="button"], button, div')

    await userEvent.click(row)
    await screen.findByRole('dialog')
    await userEvent.keyboard('{Escape}')

    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    expect(document.body).not.toBe(document.activeElement)
    expect(opener).toBeTruthy()
  })
})
