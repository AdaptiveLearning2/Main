import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { vi } from 'vitest'
import Sessions from './Sessions'

// Sessions reads the roster, then a session list per student, so the roster
// read can succeed while individual per-student reads fail.

vi.mock('../../lib/api', () => ({ apiFetch: vi.fn() }))
const { apiFetch } = await import('../../lib/api')

const CLASSES = [{ id: 'c1', name: 'Year 7' }]
const ROSTER = [
  { user_id: 'a', name: 'Ada' },
  { user_id: 'b', name: 'Blaise' },
]
const SESSION = {
  id: 's1', started_at: '2026-08-15T10:00:00Z', ended_at: '2026-08-15T10:30:00Z',
  questions_answered: 6, correct_answers: 4,
}

/** Route each call by URL. `students` maps user_id -> rows, or an Error. */
function wire({ classes = CLASSES, roster = ROSTER, students = {} }) {
  apiFetch.mockImplementation(async (path) => {
    if (path === '/api/classes') {
      if (classes instanceof Error) throw classes
      return classes
    }
    if (path.endsWith('/students')) {
      if (roster instanceof Error) throw roster
      return roster
    }
    const id = path.split('/').pop()
    const out = students[id]
    if (out instanceof Error) throw out
    return out ?? []
  })
}

const draw = () => render(<MemoryRouter><Sessions /></MemoryRouter>)

const BANNER = /couldn't be loaded, so this list is incomplete/i
const EMPTY = /no sessions yet/i
const ERROR = /couldn't load this class's sessions/i

beforeEach(() => { apiFetch.mockReset() })

it('does not call a class with no readable sessions an empty one', async () => {
  // Every per-student read fails: this should show an error, not "no sessions".
  wire({ students: { a: new Error('down'), b: new Error('down') } })

  draw()

  expect(await screen.findByText(ERROR)).toBeInTheDocument()
  expect(screen.queryByText(EMPTY)).not.toBeInTheDocument()
  // Not the partial banner either, since nothing loaded at all.
  expect(screen.queryByText(BANNER)).not.toBeInTheDocument()
})

it('still calls a class that genuinely ran no sessions empty', async () => {
  // A class can genuinely have no sessions -- not every empty result is a failure.
  wire({ students: { a: [], b: [] } })

  draw()

  expect(await screen.findByText(EMPTY)).toBeInTheDocument()
  expect(screen.queryByText(ERROR)).not.toBeInTheDocument()
  expect(screen.queryByText(BANNER)).not.toBeInTheDocument()
})

it('reports a partly-loaded class as partly loaded, and shows what it has', async () => {
  // A partial failure should show the banner and still display what loaded.
  wire({ students: { a: [SESSION], b: new Error('down') } })

  draw()

  expect(await screen.findByText(BANNER)).toBeInTheDocument()
  expect(screen.getByText('Ada')).toBeInTheDocument()
  expect(screen.queryByText(ERROR)).not.toBeInTheDocument()
  expect(screen.queryByText(EMPTY)).not.toBeInTheDocument()
})

it('offers a retry that does not need a page reload', async () => {
  // LoadError only shows its retry button when given an onRetry handler.
  wire({ students: { a: new Error('down'), b: new Error('down') } })
  draw()
  await screen.findByText(ERROR)

  wire({ students: { a: [SESSION], b: [] } })
  await userEvent.click(screen.getByRole('button', { name: /try again/i }))

  await waitFor(() => expect(screen.queryByText(ERROR)).not.toBeInTheDocument())
  expect(screen.getByText('Ada')).toBeInTheDocument()
})

it('retries the class list when that is the read that failed', async () => {
  // Retry must re-run the class list fetch here, not the per-student one --
  // with no class selected, there's no roster to retry.
  wire({ classes: new Error('down') })
  draw()
  await screen.findByText(ERROR)

  wire({ students: { a: [SESSION], b: [] } })
  await userEvent.click(screen.getByRole('button', { name: /try again/i }))

  await waitFor(() => expect(screen.queryByText(ERROR)).not.toBeInTheDocument())
  expect(screen.getByText('Ada')).toBeInTheDocument()
})
