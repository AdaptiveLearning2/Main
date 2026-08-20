import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { vi } from 'vitest'
import Sessions from './Sessions'

// Sessions is the one converted page that fans out: it reads the roster, then a
// session list *per student*. That gives it a failure mode the other seven do
// not have -- the outer read succeeding while the inner ones fail -- and it is
// the path the page's own empty state did not know about.

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
  // The roster loads and every per-student read fails -- what a degraded
  // /api/sessions/student/* looks like for the whole class rather than for one
  // child. `failed` stayed false and `allRows` was empty, so the page drew the
  // partial-failure banner directly above "No sessions yet": it told a teacher
  // both that some sessions were missing and that there were none.
  wire({ students: { a: new Error('down'), b: new Error('down') } })

  draw()

  expect(await screen.findByText(ERROR)).toBeInTheDocument()
  expect(screen.queryByText(EMPTY)).not.toBeInTheDocument()
  // And not the partial banner either. Nothing loaded, so there is nothing for
  // the list to be incomplete *about* -- two messages for one failure, one of
  // them implying the rest of the class came back fine.
  expect(screen.queryByText(BANNER)).not.toBeInTheDocument()
})

it('still calls a class that genuinely ran no sessions empty', async () => {
  // The mirror, so the branch above cannot be satisfied by treating every empty
  // class as broken. A class at the start of term really has no sessions.
  wire({ students: { a: [], b: [] } })

  draw()

  expect(await screen.findByText(EMPTY)).toBeInTheDocument()
  expect(screen.queryByText(ERROR)).not.toBeInTheDocument()
  expect(screen.queryByText(BANNER)).not.toBeInTheDocument()
})

it('reports a partly-loaded class as partly loaded, and shows what it has', async () => {
  // One student's read fails and the other's does not. This is the case the
  // banner was written for, and it has to keep working -- the fix above must
  // not turn every partial failure into a total one.
  wire({ students: { a: [SESSION], b: new Error('down') } })

  draw()

  expect(await screen.findByText(BANNER)).toBeInTheDocument()
  expect(screen.getByText('Ada')).toBeInTheDocument()
  expect(screen.queryByText(ERROR)).not.toBeInTheDocument()
  expect(screen.queryByText(EMPTY)).not.toBeInTheDocument()
})

it('offers a retry that does not need a page reload', async () => {
  // LoadError renders its button only when handed an `onRetry`, and this page
  // never extracted one from its inline .then()/.catch() -- so it was the only
  // converted page whose error state had no way out but knowing to reload.
  wire({ students: { a: new Error('down'), b: new Error('down') } })
  draw()
  await screen.findByText(ERROR)

  wire({ students: { a: [SESSION], b: [] } })
  await userEvent.click(screen.getByRole('button', { name: /try again/i }))

  await waitFor(() => expect(screen.queryByText(ERROR)).not.toBeInTheDocument())
  expect(screen.getByText('Ada')).toBeInTheDocument()
})

it('retries the class list when that is the read that failed', async () => {
  // The two reads fail differently and the button has to re-run the right one.
  // With no class selected there is no roster to re-fetch, so retrying the
  // inner call would leave the page on its error state for ever.
  wire({ classes: new Error('down') })
  draw()
  await screen.findByText(ERROR)

  wire({ students: { a: [SESSION], b: [] } })
  await userEvent.click(screen.getByRole('button', { name: /try again/i }))

  await waitFor(() => expect(screen.queryByText(ERROR)).not.toBeInTheDocument())
  expect(screen.getByText('Ada')).toBeInTheDocument()
})

it('does not flash "no sessions yet" between a successful retry and the roster', async () => {
  // The retry path only. `loading` starts true, so the *first* load never showed
  // this -- which is why it survived. On a retry `loading` is already false, and
  // `loadClasses` picking a class programmatically left the page with no rows,
  // no error and no skeleton for as long as the roster took to arrive.
  const classes = new Error('down')
  wire({ classes })

  draw()
  await screen.findByText(ERROR)

  // The class list now works; the roster is held open so the gap is observable.
  let releaseRoster
  apiFetch.mockImplementation(async (path) => {
    if (path === '/api/classes') return CLASSES
    if (path.endsWith('/students')) return new Promise(r => { releaseRoster = r })
    return []
  })

  await userEvent.click(screen.getByRole('button', { name: /try again/i }))

  // The roster has not landed. Whatever is on screen must not be an assertion
  // that this class has no sessions.
  await waitFor(() => expect(releaseRoster).toBeTypeOf('function'))
  expect(screen.queryByText(EMPTY)).not.toBeInTheDocument()

  releaseRoster(ROSTER)
  await waitFor(() => expect(screen.queryByText(EMPTY)).not.toBeInTheDocument())
})

it('does not let a slow class roster repaint the list under a newer class', async () => {
  // This fetch fans out per student, so it is the slowest on the page by some
  // margin -- switching class while one is in flight let the *previous* class's
  // response land last and repaint the list, with the dropdown still reading
  // the class you picked. The same bug class this file was patched for, one
  // function further down.
  const releases = {}
  apiFetch.mockImplementation(async (path) => {
    if (path === '/api/classes') return [{ id: 'c1', name: 'Year 7' }, { id: 'c2', name: 'Year 8' }]
    if (path.endsWith('/students')) {
      const id = path.split('/')[3]
      return new Promise(r => { releases[id] = r })
    }
    return [SESSION]
  })

  draw()
  await waitFor(() => expect(releases.c1).toBeTypeOf('function'))

  await userEvent.selectOptions(screen.getByRole('combobox'), 'c2')
  await waitFor(() => expect(releases.c2).toBeTypeOf('function'))

  // The newer class answers first, then the older one lands.
  releases.c2([{ user_id: 'z', name: 'Zola' }])
  await screen.findByText('Zola')

  releases.c1([{ user_id: 'a', name: 'Ada' }])
  await waitFor(() => expect(screen.getByText('Zola')).toBeInTheDocument())
  expect(screen.queryByText('Ada')).not.toBeInTheDocument()
})
