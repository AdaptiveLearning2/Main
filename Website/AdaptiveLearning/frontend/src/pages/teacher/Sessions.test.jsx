import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { vi } from 'vitest'
import Sessions from './Sessions'

// One read per class; a student whose sessions could not be read comes back `null`.

vi.mock('../../lib/api', () => ({ apiFetch: vi.fn() }))
const { apiFetch } = await import('../../lib/api')

const CLASSES = [{ id: 'c1', name: 'Year 7' }]
const ROSTER = [
  { user_id: 'a', name: 'Ada', email: 'ada@example.test' },
  { user_id: 'b', name: 'Blaise', email: 'blaise@example.test' },
]
const SESSION = {
  id: 's1', started_at: '2026-08-15T10:00:00Z', ended_at: '2026-08-15T10:30:00Z',
  questions_answered: 6, correct_answers: 4,
}

/** What `/api/classes/{id}/sessions` returns: `students` maps user_id -> rows or null. */
const classSessions = (roster, students = {}) => ({
  students: roster,
  sessions: Object.fromEntries(roster.map(s => [s.user_id, s.user_id in students ? students[s.user_id] : []])),
})

/** Route each call by URL; an unrouted path throws rather than reading as an empty success. */
function wire({ classes = CLASSES, roster = ROSTER, students = {} }) {
  apiFetch.mockImplementation(async (path) => {
    if (path === '/api/classes') {
      if (classes instanceof Error) throw classes
      return classes
    }
    if (/^\/api\/classes\/[^/]+\/sessions$/.test(path)) {
      if (roster instanceof Error) throw roster
      return classSessions(roster, students)
    }
    throw new Error(`unrouted ${path}`)
  })
}

const draw = () => render(<MemoryRouter><Sessions /></MemoryRouter>)

const BANNER = /couldn't be loaded, so this list is incomplete/i
const EMPTY = /no sessions yet/i
const ERROR = /couldn't load this class's sessions/i

beforeEach(() => { apiFetch.mockReset() })

it('does not call a class with no readable sessions an empty one', async () => {
  wire({ students: { a: null, b: null } })

  draw()

  expect(await screen.findByText(ERROR)).toBeInTheDocument()
  expect(screen.queryByText(EMPTY)).not.toBeInTheDocument()
  // Not the partial banner either: nothing loaded.
  expect(screen.queryByText(BANNER)).not.toBeInTheDocument()
  // The backend answered; its read failed. So "just now", never "make sure the backend is running".
  expect(screen.getByText(/just now/i)).toBeInTheDocument()
  expect(screen.queryByText(/backend is running/i)).not.toBeInTheDocument()
})

it('still calls a class that genuinely ran no sessions empty', async () => {
  wire({ students: { a: [], b: [] } })

  draw()

  expect(await screen.findByText(EMPTY)).toBeInTheDocument()
  expect(screen.queryByText(ERROR)).not.toBeInTheDocument()
  expect(screen.queryByText(BANNER)).not.toBeInTheDocument()
})

it('reports a partly-loaded class as partly loaded, and shows what it has', async () => {
  wire({ students: { a: [SESSION], b: null } })

  draw()

  expect(await screen.findByText(BANNER)).toBeInTheDocument()
  // `findByText`: the class list and roster are two fetches, so rows can trail the banner.
  expect(await screen.findByText('Ada')).toBeInTheDocument()
  expect(screen.queryByText(ERROR)).not.toBeInTheDocument()
  expect(screen.queryByText(EMPTY)).not.toBeInTheDocument()
})

it('draws a nameless student as their email, else "No name set", never "Student"', async () => {
  // `name` and `email` are null when unset, as the roster sends them.
  wire({
    roster: [
      { user_id: 'a', name: 'Ada', email: 'ada@example.test' },
      { user_id: 'b', name: null, email: 'blaise@example.test' },
      { user_id: 'c', name: null, email: null },
    ],
    students: { a: [SESSION], b: [{ ...SESSION, id: 's2' }], c: [{ ...SESSION, id: 's3' }] },
  })
  draw()
  // Every row lands in one render, so the rest are there once the first is.
  await screen.findByText('Ada')
  // The initial comes from the label shown, and is "?" with nothing to show.
  for (const [label, initial] of [['Ada', 'A'], ['blaise@example.test', 'B'], ['No name set', '?']]) {
    expect(within(screen.getByText(label).closest('a')).getByText(initial)).toBeInTheDocument()
  }
  expect(screen.getAllByRole('link').filter(row => row.textContent.includes('Student'))).toEqual([])
})

it('finds a nameless student by the email their row shows', async () => {
  // Ada's email does not contain her name, so a filter on email alone cannot find her.
  wire({
    roster: [
      { user_id: 'a', name: 'Ada', email: 'lovelace@example.test' },
      { user_id: 'b', name: null, email: 'blaise@example.test' },
    ],
    students: { a: [SESSION], b: [{ ...SESSION, id: 's2' }] },
  })
  draw()
  await screen.findByText('Ada')
  const box = screen.getByPlaceholderText(/filter by student/i)
  await userEvent.type(box, 'blaise@')
  expect(screen.getByText('blaise@example.test')).toBeInTheDocument()
  expect(screen.queryByText('Ada')).not.toBeInTheDocument()

  await userEvent.clear(box)
  await userEvent.type(box, 'ada')
  expect(screen.getByText('Ada')).toBeInTheDocument()
  expect(screen.queryByText('blaise@example.test')).not.toBeInTheDocument()
})

it('shows the load error, not rows named "Student", when the class read is a 503', async () => {
  // A failed name read is a 503 from the backend, never placeholder names.
  wire({ roster: Object.assign(new Error('Could not load this class\'s students'), { status: 503 }) })
  draw()
  expect(await screen.findByText(ERROR)).toBeInTheDocument()
  expect(screen.getByText(/just now/i)).toBeInTheDocument()
  expect(screen.queryByText(/backend is running/i)).not.toBeInTheDocument()
  expect(screen.queryByRole('link')).not.toBeInTheDocument()
})

it('reads the whole class in one request, not one per student', async () => {
  wire({ students: { a: [SESSION], b: [] } })
  draw()
  await screen.findByText('Ada')
  expect(apiFetch.mock.calls.map(([path]) => path)).toEqual(['/api/classes', '/api/classes/c1/sessions'])
})

it('counts a student missing from the payload as unread, not as no sessions', async () => {
  apiFetch.mockImplementation(async (path) => (path === '/api/classes'
    ? CLASSES : { students: ROSTER, sessions: { a: [SESSION] } }))
  draw()
  expect(await screen.findByText(BANNER)).toBeInTheDocument()
})

it('offers a retry that does not need a page reload', async () => {
  // LoadError only shows its retry button when given an onRetry handler.
  wire({ students: { a: null, b: null } })
  draw()
  await screen.findByText(ERROR)

  wire({ students: { a: [SESSION], b: [] } })
  await userEvent.click(screen.getByRole('button', { name: /try again/i }))

  await waitFor(() => expect(screen.queryByText(ERROR)).not.toBeInTheDocument())
  // `findByText`: the error clears before the roster arrives.
  expect(await screen.findByText('Ada')).toBeInTheDocument()
})

it('retries the class list when that is the read that failed', async () => {
  // With no class selected there is no roster to retry.
  wire({ classes: new Error('down') })
  draw()
  await screen.findByText(ERROR)

  wire({ students: { a: [SESSION], b: [] } })
  await userEvent.click(screen.getByRole('button', { name: /try again/i }))

  await waitFor(() => expect(screen.queryByText(ERROR)).not.toBeInTheDocument())
  // `findByText`: the error clears before the roster arrives.
  expect(await screen.findByText('Ada')).toBeInTheDocument()
})

it('does not flash "no sessions yet" between a successful retry and the roster', async () => {
  // On a retry `loading` is already false, unlike the first load.
  const classes = new Error('down')
  wire({ classes })

  draw()
  await screen.findByText(ERROR)

  // The class list now works; the roster is held open so the gap is observable.
  let releaseRoster
  apiFetch.mockImplementation(async (path) => {
    if (path === '/api/classes') return CLASSES
    if (path.endsWith('/sessions')) return new Promise(r => { releaseRoster = r })
    throw new Error(`unrouted ${path}`)
  })

  await userEvent.click(screen.getByRole('button', { name: /try again/i }))

  await waitFor(() => expect(releaseRoster).toBeTypeOf('function'))
  expect(screen.queryByText(EMPTY)).not.toBeInTheDocument()

  releaseRoster(classSessions(ROSTER))
  await waitFor(() => expect(screen.queryByText(EMPTY)).not.toBeInTheDocument())
})

it('does not let a slow class roster repaint the list under a newer class', async () => {
  const releases = {}
  apiFetch.mockImplementation(async (path) => {
    if (path === '/api/classes') return [{ id: 'c1', name: 'Year 7' }, { id: 'c2', name: 'Year 8' }]
    if (path.endsWith('/sessions')) {
      const id = path.split('/')[3]
      return new Promise(r => { releases[id] = r })
    }
    throw new Error(`unrouted ${path}`)
  })

  draw()
  await waitFor(() => expect(releases.c1).toBeTypeOf('function'))

  await userEvent.selectOptions(screen.getByRole('combobox'), 'c2')
  await waitFor(() => expect(releases.c2).toBeTypeOf('function'))

  // The newer class answers first, then the older one lands.
  releases.c2(classSessions([{ user_id: 'z', name: 'Zola' }], { z: [SESSION] }))
  await screen.findByText('Zola')

  releases.c1(classSessions([{ user_id: 'a', name: 'Ada' }], { a: [SESSION] }))
  await waitFor(() => expect(screen.getByText('Zola')).toBeInTheDocument())
  expect(screen.queryByText('Ada')).not.toBeInTheDocument()
})

// ─── abandoned sessions ────────────────────────────────────────────────────
// The backend decides `abandoned`, so the threshold has one definition.

const OPEN_RECENT = {
  id: 's-live', started_at: '2026-08-15T10:00:00Z', ended_at: null,
  questions_answered: 3, correct_answers: 2, abandoned: false,
}
const OPEN_ABANDONED = {
  id: 's-old', started_at: '2026-06-29T01:24:00Z', ended_at: null,
  questions_answered: 0, correct_answers: 0, abandoned: true,
}

it('does not call a two-month-old open session live', async () => {
  wire({ students: { a: [OPEN_ABANDONED], b: [] } })
  draw()
  expect(await screen.findByText(/never ended/i)).toBeInTheDocument()
  expect(screen.queryByText(/LIVE/)).not.toBeInTheDocument()
})

it('still calls a genuinely open session live', async () => {
  // Teeth for the negative above.
  wire({ students: { a: [OPEN_RECENT], b: [] } })
  draw()
  expect(await screen.findByText(/LIVE/)).toBeInTheDocument()
  expect(screen.queryByText(/never ended/i)).not.toBeInTheDocument()
})

it('shows no duration for an abandoned session', async () => {
  wire({ students: { a: [OPEN_ABANDONED], b: [] } })
  draw()
  await screen.findByText(/never ended/i)
  expect(screen.queryByText(/\d+m \d+s/)).not.toBeInTheDocument()
})

it('an abandoned session is not reported as done either', async () => {
  // Never ended, so its questions were never credited.
  wire({ students: { a: [OPEN_ABANDONED], b: [] } })
  draw()
  await screen.findByText(/never ended/i)
  expect(screen.queryByText(/done/i)).not.toBeInTheDocument()
})

// ─── idle sessions ─────────────────────────────────────────────────────────
// `last_activity_at` is the newest answer, so an idle duration ends there, not at now.

const OPEN_IDLE = {
  id: 's-idle', started_at: '2026-08-15T10:00:00Z', ended_at: null,
  questions_answered: 4, correct_answers: 3, abandoned: false,
  idle: true, activity_known: true,
  last_activity_at: '2026-08-15T10:23:00Z',
}

it('measures an idle session to its last activity, not to now', async () => {
  wire({ students: { a: [OPEN_IDLE], b: [] } })
  draw()
  await screen.findByText(/idle/i)
  // 10:00 -> 10:23; counting to now could never equal this.
  expect(screen.getByText('23m 0s')).toBeInTheDocument()
})

it('shows no duration for an idle session whose last activity is unknown', async () => {
  wire({ students: { a: [{ ...OPEN_IDLE, last_activity_at: null }], b: [] } })
  draw()
  await screen.findByText(/idle/i)
  expect(screen.queryByText(/\d+m \d+s/)).not.toBeInTheDocument()
})

it('still counts a live session to now', async () => {
  // Teeth for the negatives above.
  wire({ students: { a: [OPEN_RECENT], b: [] } })
  draw()
  await screen.findByText(/LIVE/)
  expect(screen.getByText(/\d+m \d+s/)).toBeInTheDocument()
})

it('treats a payload with no abandoned flag as live, not abandoned', async () => {
  // An older backend does not send the field.
  const { abandoned, ...noFlag } = OPEN_RECENT   // eslint-disable-line no-unused-vars
  wire({ students: { a: [noFlag], b: [] } })
  draw()
  expect(await screen.findByText(/LIVE/)).toBeInTheDocument()
})
