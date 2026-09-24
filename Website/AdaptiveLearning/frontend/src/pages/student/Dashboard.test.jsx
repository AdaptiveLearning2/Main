import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, it, expect, beforeEach, vi } from 'vitest'
import { act, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'

vi.mock('../../lib/api', async () => await import('../../test/mocks/apiFetch'))

const navigate = vi.fn()
vi.mock('react-router-dom', async () => ({
  ...await vi.importActual('react-router-dom'),
  useNavigate: () => navigate,
}))

vi.mock('../../context/AuthContext', () => ({
  useAuth: () => ({ user: { id: 'stu-1', email: 'kid@example.com' },
                    displayName: authName }),
}))
// Set per test; the page renders the given name rather than deriving one.
let authName = 'Ada Lovelace'

// Mocked because these banners fetch on mount but aren't under test here.
vi.mock('../../components/consent/ParentRestoredBanner', () => ({ default: () => null }))
vi.mock('../../components/consent/ParentLinkedBanner', () => ({ default: () => null }))

import { apiFetch, mockApi, overrideApi, resetApi, apiError } from '../../test/mocks/apiFetch'
import Dashboard from './Dashboard'

const STATS = { total_questions: 40, total_correct: 30, current_streak: 2, best_streak: 9 }
const BREAKDOWN = '/api/students/stu-1/topic-breakdown'
const SESSIONS = '/api/sessions?limit=4'

const topic = (topic_name, accuracy, attempted_questions) => ({
  topic_name, accuracy, attempted_questions, correct_questions: 0,
})

const draw = () => render(<MemoryRouter><Dashboard /></MemoryRouter>)

// Waits for that exact request to settle: until then the hook answers `null` either way.
async function settled(path) {
  await waitFor(() => expect(apiFetch).toHaveBeenCalledWith(path))
  const i = apiFetch.mock.calls.findIndex(([p]) => p === path)
  await act(async () => { await apiFetch.mock.results[i].value.catch(() => {}) })
}

beforeEach(() => {
  resetApi()
  navigate.mockReset()
  mockApi({
    '/api/stats/me': () => STATS,
    // Keyed with the query string: the router matches exactly.
    [SESSIONS]: () => ({ sessions: [], total: 0, truncated: false }),
    '/api/profile/me': () => ({ practice_reminders: false }),
    [BREAKDOWN]: () => [
      topic('algebra', 30, 10),
      topic('geometry', 90, 10),
    ],
  })
})

describe('the topic grid', () => {
  it('shows each measured topic as a number, not only a colour', async () => {
    draw()

    expect(await screen.findByText('30%')).toBeInTheDocument()
    expect(screen.getByText('90%')).toBeInTheDocument()
  })

  it('leaves a topic with no attempts plain, saying nothing about it', async () => {
    draw()
    await screen.findByText('30%')

    // `mode` is not in the payload.
    expect(screen.getByText('mode')).toBeInTheDocument()
    expect(screen.queryByText('0%')).not.toBeInTheDocument()
  })

  // `/api/topics` answers for every topic; `allowed` is the backend's grade gate.
  const GRADE_ONE = [
    { name: 'ordering', allowed: true }, { name: 'algebra', allowed: false },
    { name: 'geometry', allowed: false }, { name: 'mode', allowed: false },
    { name: 'counting', allowed: false },
  ]

  it("lists the grade's topics, and keeps any the student has already attempted", async () => {
    overrideApi('/api/profile/me', () => ({ practice_reminders: false, grade_level: '1st Grade' }))
    overrideApi('/api/topics?grade=1st%20Grade', () => GRADE_ONE)

    draw()

    // The tiles only: the weakest-topic panel in the same card also names a topic.
    const grid = () => screen.getByText('Topics in the Curriculum').nextElementSibling
    expect(await screen.findByText('ordering')).toBeInTheDocument()
    await waitFor(() => expect(within(grid()).queryByText('mode')).not.toBeInTheDocument())
    expect(within(grid()).queryByText('counting')).not.toBeInTheDocument()
    // Not grade 1's, but attempted: a student's own record never disappears.
    expect(within(grid()).getByText('algebra')).toBeInTheDocument()
    expect(within(grid()).getByText('geometry')).toBeInTheDocument()
  })

  it('lists grade 1 for a profile with no grade, as the backend serves it', async () => {
    // No `grade` parameter: `_allowed_topics(None)` is grade 1's list.
    overrideApi('/api/topics', () => GRADE_ONE)

    draw()

    await settled('/api/topics')
    const grid = screen.getByText('Topics in the Curriculum').nextElementSibling
    expect(within(grid).getByText('ordering')).toBeInTheDocument()
    expect(within(grid).queryByText('mode')).not.toBeInTheDocument()
  })

  it('asks for no topic list, and lists every topic, when the profile could not be read', async () => {
    overrideApi('/api/profile/me', () => { throw apiError(500, 'down') })

    draw()

    await screen.findByText('30%')
    expect(screen.getByText('mode')).toBeInTheDocument()
    expect(apiFetch.mock.calls.some(([p]) => p.startsWith('/api/topics'))).toBe(false)
  })

  it('lists every topic when the grade could not be looked up', async () => {
    overrideApi('/api/profile/me', () => ({ practice_reminders: false, grade_level: '1st Grade' }))
    overrideApi('/api/topics?grade=1st%20Grade', () => { throw apiError(500, 'down') })

    draw()

    await screen.findByText('30%')
    await settled('/api/topics?grade=1st%20Grade')
    expect(screen.getByText('mode')).toBeInTheDocument()
    expect(screen.getByText('counting')).toBeInTheDocument()
  })

  it('draws the same plain tiles when the breakdown could not be read', async () => {
    overrideApi(BREAKDOWN, () => { throw apiError(500, 'down') })

    draw()

    expect(await screen.findByText('algebra')).toBeInTheDocument()
    expect(screen.queryByText('30%')).not.toBeInTheDocument()
    expect(screen.queryByText(/weakest so far/i)).not.toBeInTheDocument()
  })
})

describe('the weakest topic', () => {
  it('names the lowest-scoring topic and offers to practise it', async () => {
    draw()

    const cta = await screen.findByRole('button', { name: /weakest so far/i })
    expect(cta).toHaveTextContent(/algebra/i)

    await userEvent.click(cta)
    expect(navigate).toHaveBeenCalledWith('/adaptive')
  })

  it('will not call a topic weakest off one or two attempts', async () => {
    overrideApi(BREAKDOWN, () => ([
      topic('algebra', 0, 1),
      topic('geometry', 70, 10),
    ]))

    draw()

    const cta = await screen.findByRole('button', { name: /weakest so far/i })
    expect(cta).toHaveTextContent(/geometry/i)
  })

  it('says nothing at all when nothing has enough attempts', async () => {
    overrideApi(BREAKDOWN, () => ([topic('algebra', 0, 1)]))

    draw()
    await screen.findByText('algebra')

    expect(screen.queryByRole('button', { name: /weakest so far/i })).not.toBeInTheDocument()
  })
})

describe('the streak card', () => {
  it('shows the personal best when the current streak is below it', async () => {
    draw()
    expect(await screen.findByText('best 9 days')).toBeInTheDocument()
  })

  it('does not repeat the number back while the student is on their best run', async () => {
    overrideApi('/api/stats/me', () => ({ ...STATS, current_streak: 9, best_streak: 9 }))

    draw()

    await waitFor(() => expect(screen.getByText('9')).toBeInTheDocument())
    expect(screen.queryByText(/best 9 days/)).not.toBeInTheDocument()
  })
})

describe('the topic tiles', () => {
  // Both lists come from `lib/topics.js`; `lib/topics.test.js` checks them against the backend.
  it('take their icons from the shared list', async () => {
    const { TOPICS, TOPIC_ICONS } = await import('../../lib/topics')
    const source = readFileSync(
      resolve(process.cwd(), 'src/pages/student/Dashboard.jsx'), 'utf8')
    expect(source).toContain("from '../../lib/topics'")
    expect(TOPICS.filter(t => !TOPIC_ICONS[t])).toEqual([])
  })
})

it('greets the student by their stored name, never by their email', async () => {
  authName = 'Ada Lovelace'
  draw()
  expect(await screen.findByText('Ada Lovelace')).toBeInTheDocument()
  expect(screen.queryByText('kid')).not.toBeInTheDocument()
})

it('greets a nameless account without addressing it as nobody', async () => {
  // `displayName` is null only with no stored name, claim or email.
  authName = null
  draw()
  expect(await screen.findByText(/there/)).toBeInTheDocument()
})

// ── the recent-sessions panel ────────────────────────────────────────────
// Four rows shown, four asked for; a read that did not land is not "No sessions yet".

describe('the recent-sessions panel', () => {
  it('asks for the four rows it shows and no more', async () => {
    draw()

    await screen.findByText(/no sessions yet/i)
    expect(apiFetch).toHaveBeenCalledWith(SESSIONS)
    expect(apiFetch).not.toHaveBeenCalledWith('/api/sessions')
  })

  it.each([
    ['a rejected read', () => { throw apiError(500, 'down') }],
    ['a body it does not read', () => [{ id: 's1', started_at: '2026-09-22T10:00:00Z' }]],
  ])('says %s could not be loaded, rather than that there are none', async (_name, handler) => {
    mockApi({
      '/api/stats/me': () => STATS,
      [SESSIONS]: handler,
      '/api/profile/me': () => ({ practice_reminders: true }),
      [BREAKDOWN]: () => [],
    })
    draw()

    expect(await screen.findByText(/sessions couldn.t be loaded/i)).toBeInTheDocument()
    expect(screen.queryByText(/no sessions yet/i)).not.toBeInTheDocument()
    // Reminders are on, and still no nudge from a read that never landed.
    expect(screen.queryByText(/not practised today/i)).not.toBeInTheDocument()
  })
})
