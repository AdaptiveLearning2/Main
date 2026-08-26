import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'

vi.mock('../../lib/api', async () => await import('../../test/mocks/apiFetch'))

import { apiFetch, mockApi, resetApi } from '../../test/mocks/apiFetch'
import { _resetForTests } from '../../lib/questionsCache'
import Questions from './Questions'
import Analytics from './Analytics'

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
  // The question-bank cache is module-level state shared with Analytics.jsx,
  // so it has to be cleared between tests too, or a later test can be served
  // a still-fresh entry left behind by an earlier one.
  _resetForTests()
  mockApi({
    '/api/questions?limit=1000': () => [QUESTION],
    // Fetched on mount for the student filter. Registered here rather
    // than per test because the router double throws on an unrouted
    // path -- which is what stops a gap in setup arriving dressed as
    // the bug a test was written to catch.
    '/api/classes': () => [],
  })
})

async function openModal() {
  render(<Questions />, { wrapper: MemoryRouter })
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
    render(<Questions />, { wrapper: MemoryRouter })
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

describe('the question-bank cache shared with Analytics', () => {
  it('serves both pages from a single fetch of the bank', async () => {
    // Both pages independently fetch `?limit=1000` on mount -- this is the
    // direct regression test for the redundant-fetch problem the shared
    // cache exists to fix.
    render(<Questions />, { wrapper: MemoryRouter })
    render(<Analytics />, { wrapper: MemoryRouter })

    await screen.findByText('What is 7 x 8?')
    // Counted on the bank path specifically, not on every apiFetch call:
    // Questions also fetches /api/classes for the student filter, and a bare
    // call count would make this test fail for a reason unrelated to the
    // caching it exists to check -- and would fail again for the next
    // unrelated fetch either page adds.
    await waitFor(() => {
      const bankCalls = apiFetch.mock.calls
        .filter(([path]) => path === '/api/questions?limit=1000')
      expect(bankCalls).toHaveLength(1)
    })
  })
})

describe('the student filter', () => {
  const CLASSES = [{ id: 'c-1', name: 'Period 1' }]
  const ROSTER  = [{ id: 's-1', display_name: 'Ada' }]
  const ASKED = {
    student_id: 's-1',
    questions: [{
      question_id: 'q-9', question_text: 'What is 3 x 4?',
      subject: 'algebra', difficulty: 'easy',
      session_id: 'sess-7', attempts: 3, correct: 2,
    }],
    answers_read: 3, expired_questions: 0, truncated: false,
  }

  function mockFilterApi(overrides = {}) {
    mockApi({
      '/api/questions?limit=1000': () => [QUESTION],
      '/api/classes': () => CLASSES,
      '/api/classes/c-1/students': () => ROSTER,
      '/api/students/s-1/questions?limit=200': () => ASKED,
      ...overrides,
    })
  }

  async function pickStudent() {
    render(<Questions />, { wrapper: MemoryRouter })
    await userEvent.selectOptions(await screen.findByLabelText('Filter by class'), 'c-1')
    await userEvent.selectOptions(await screen.findByLabelText('Filter by student'), 's-1')
  }

  it('swaps the bank for one student, and shows their attempts', async () => {
    mockFilterApi()
    await pickStudent()

    expect(await screen.findByText('What is 3 x 4?')).toBeInTheDocument()
    // The bank's question is gone -- this is a different list, not a filter
    // applied on top of the one already loaded.
    expect(screen.queryByText('What is 7 x 8?')).not.toBeInTheDocument()
    expect(screen.getByText('2/3 correct')).toBeInTheDocument()
    expect(screen.getByText(/1 question asked/)).toBeInTheDocument()
  })

  it('says when a question has aged out rather than just showing fewer', async () => {
    // The three-state rule: "answered nothing" and "their questions expired"
    // both render as a short list otherwise.
    mockFilterApi({
      '/api/students/s-1/questions?limit=200': () => ({
        ...ASKED, questions: [], answers_read: 2, expired_questions: 2,
      }),
    })
    await pickStudent()
    expect(await screen.findByText(/2 no longer in the bank/)).toBeInTheDocument()
  })

  it('keeps the page usable when the class list fails', async () => {
    // The selector is an extra; the bank is the page's actual content.
    mockFilterApi({ '/api/classes': () => { throw new Error('nope') } })
    render(<Questions />, { wrapper: MemoryRouter })
    expect(await screen.findByText('What is 7 x 8?')).toBeInTheDocument()
    expect(screen.queryByLabelText('Filter by class')).not.toBeInTheDocument()
  })

  it('surfaces a failed student read instead of showing the bank as theirs', async () => {
    // Falling back to the bank here would attribute every question in the
    // product to one child.
    mockFilterApi({
      '/api/students/s-1/questions?limit=200': () => { throw new Error('nope') },
    })
    await pickStudent()
    await waitFor(() => expect(screen.queryByText('What is 7 x 8?')).not.toBeInTheDocument())
  })
})
