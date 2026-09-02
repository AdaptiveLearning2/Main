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
  // The shape `/api/classes/{id}/students` really returns. It carries
  // `user_id` and `name`; it has no `id` and no `display_name`. This fixture
  // said otherwise for as long as the picker read those keys, so the code and
  // the test shared one misreading of the backend and the suite stayed green
  // over a filter that answered 403 on every pick. `email` is here because it
  // is what the broken version actually sent -- an `<option>` with an
  // undefined `value` falls back to its text content -- so without it the
  // failure is not even representable.
  const ROSTER  = [{ user_id: 's-1', name: 'Ada', email: 'ada@example.com' }]
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

  it('asks for the student by id, never by the name on the option', async () => {
    // `/api/students/{id}/questions` resolves a relationship through
    // `_verify_can_view_student`, so an identifier that is merely
    // recognisable -- an email, a display name -- is refused with a 403 by a
    // backend that is working correctly. The page then reports that the
    // backend is down, which is the wrong thing to go and check.
    //
    // Asserting on the *path* rather than on the rendered rows is the point:
    // both halves of the option (`key`/`value` and the label) come from the
    // same row, so a wrong key still names the right student on screen.
    mockFilterApi()
    await pickStudent()

    const asked = apiFetch.mock.calls.map(c => c[0])
      .filter(p => p.startsWith('/api/students/'))
    expect(asked).toEqual(['/api/students/s-1/questions?limit=200'])
    expect(asked.join()).not.toContain('ada@example.com')
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

  it('names the student, not the bank, when the refusal is about one student', async () => {
    // The bank is public-read; a 403 here can only ever be about the student.
    // Saying "you don't have access to the question bank" would deny access to
    // something this teacher demonstrably has -- it is on the screen behind
    // the message.
    mockFilterApi({
      '/api/students/s-1/questions?limit=200': () => {
        throw Object.assign(new Error('Forbidden'), { status: 403 })
      },
    })
    await pickStudent()
    const box = await screen.findByRole('status')
    expect(box).toHaveTextContent("You don't have access to this student's questions.")
    expect(box).not.toHaveTextContent(/backend/i)
  })

  it('still blames the backend when the bank itself is unreachable', async () => {
    // The other direction, and the reason the wording is chosen per read
    // rather than per page: nothing about the student filter is involved here.
    mockApi({
      '/api/questions?limit=1000': () => { throw new Error('network down') },
      '/api/classes': () => CLASSES,
    })
    render(<Questions />, { wrapper: MemoryRouter })
    expect(await screen.findByRole('status'))
      .toHaveTextContent("Couldn't load the question bank. Make sure the backend is running.")
  })
})

describe('a superseded read cannot paint under the wrong name', () => {
  const CLASSES = [{ id: 'c-1', name: 'Period 1' }]
  const ROSTER  = [
    { user_id: 's-1', name: 'Ada', email: 'ada@example.com' },
    { user_id: 's-2', name: 'Grace', email: 'grace@example.com' },
  ]
  const asked = (text) => ({
    student_id: 'x',
    questions: [{ question_id: `q-${text}`, question_text: text, subject: 'algebra',
                  difficulty: 'easy', session_id: 'sess-1', attempts: 1, correct: 1 }],
    answers_read: 1, expired_questions: 0, truncated: false,
  })

  async function pick(label, value) {
    await userEvent.selectOptions(await screen.findByLabelText(label), value)
  }

  it("shows the student the dropdown says, not whichever request lands last", async () => {
    // Hold Ada's request open, switch to Grace, then release Ada's. Without a
    // supersede guard Ada's questions arrive last and paint under Grace.
    let releaseAda
    mockApi({
      '/api/questions?limit=1000': () => [QUESTION],
      '/api/classes': () => CLASSES,
      '/api/classes/c-1/students': () => ROSTER,
      '/api/students/s-1/questions?limit=200': () =>
        new Promise(res => { releaseAda = () => res(asked('ADA ONLY')) }),
      '/api/students/s-2/questions?limit=200': () => asked('GRACE ONLY'),
    })

    render(<Questions />, { wrapper: MemoryRouter })
    await pick('Filter by class', 'c-1')
    await pick('Filter by student', 's-1')
    await waitFor(() => expect(releaseAda).toBeDefined())

    await pick('Filter by student', 's-2')
    expect(await screen.findByText('GRACE ONLY')).toBeInTheDocument()

    releaseAda()
    // Ada's response resolves now. It must be discarded.
    await waitFor(() => expect(screen.getByText('GRACE ONLY')).toBeInTheDocument())
    expect(screen.queryByText('ADA ONLY')).not.toBeInTheDocument()
  })

  it("the bank landing late cannot overwrite a student's list", async () => {
    // The likelier direction: the bank resolves from the 30s cache, so going
    // back to "Whole bank" and straight into a student can land bank-last.
    let releaseStudent
    mockApi({
      '/api/questions?limit=1000': () => [QUESTION],
      '/api/classes': () => CLASSES,
      '/api/classes/c-1/students': () => ROSTER,
      '/api/students/s-1/questions?limit=200': () =>
        new Promise(res => { releaseStudent = () => res(asked('ADA ONLY')) }),
    })

    render(<Questions />, { wrapper: MemoryRouter })
    await screen.findByText('What is 7 x 8?')
    await pick('Filter by class', 'c-1')
    await pick('Filter by student', 's-1')
    await waitFor(() => expect(releaseStudent).toBeDefined())

    // Back to the bank while the student read is still open, then release it.
    await pick('Filter by student', '')
    expect(await screen.findByText('What is 7 x 8?')).toBeInTheDocument()

    releaseStudent()
    await waitFor(() => expect(screen.getByText('What is 7 x 8?')).toBeInTheDocument())
    expect(screen.queryByText('ADA ONLY')).not.toBeInTheDocument()
  })
})
