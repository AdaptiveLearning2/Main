import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'

vi.mock('../../lib/api', async () => await import('../../test/mocks/apiFetch'))

const navigate = vi.fn()
vi.mock('react-router-dom', async () => ({
  ...await vi.importActual('react-router-dom'),
  useNavigate: () => navigate,
}))

vi.mock('../../context/AuthContext', () => ({
  useAuth: () => ({ user: { id: 'stu-1', email: 'kid@example.com' } }),
}))

// Mocked because these banners fetch on mount but aren't under test here.
vi.mock('../../components/consent/ParentRestoredBanner', () => ({ default: () => null }))
vi.mock('../../components/consent/ParentLinkedBanner', () => ({ default: () => null }))

import { mockApi, overrideApi, resetApi, apiError } from '../../test/mocks/apiFetch'
import Dashboard from './Dashboard'

const STATS = { total_questions: 40, total_correct: 30, current_streak: 2, best_streak: 9 }
const BREAKDOWN = '/api/students/stu-1/topic-breakdown'

const topic = (topic_name, accuracy, attempted_questions) => ({
  topic_name, accuracy, attempted_questions, correct_questions: 0,
})

const draw = () => render(<MemoryRouter><Dashboard /></MemoryRouter>)

beforeEach(() => {
  resetApi()
  navigate.mockReset()
  mockApi({
    '/api/stats/me': () => STATS,
    '/api/sessions': () => [],
    '/api/profile/me': () => ({ practice_reminders: false }),
    [BREAKDOWN]: () => [
      topic('algebra', 30, 10),
      topic('geometry', 90, 10),
    ],
  })
})

describe('the topic grid', () => {
  it('shows each measured topic as a number, not only a colour', async () => {
    // Color alone isn't accessible to everyone, so accuracy must show as text too.
    draw()

    expect(await screen.findByText('30%')).toBeInTheDocument()
    expect(screen.getByText('90%')).toBeInTheDocument()
  })

  it('leaves a topic with no attempts plain, saying nothing about it', async () => {
    draw()
    await screen.findByText('30%')

    // `mode` isn't in the payload, so it should still show as a topic but with no figure.
    expect(screen.getByText('mode')).toBeInTheDocument()
    expect(screen.queryByText('0%')).not.toBeInTheDocument()
  })

  it('draws the same plain tiles when the breakdown could not be read', async () => {
    // A failed breakdown read should render the same as an unmeasured tile.
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
    // A single wrong answer is 0% and shouldn't be enough to rank a topic weakest.
    overrideApi(BREAKDOWN, () => ([
      topic('algebra', 0, 1),
      topic('geometry', 70, 10),
    ]))

    draw()

    // Geometry is the only topic with enough attempts to rank.
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
    // "best 9" under a 9 would look redundant rather than like an achievement.
    overrideApi('/api/stats/me', () => ({ ...STATS, current_streak: 9, best_streak: 9 }))

    draw()

    await waitFor(() => expect(screen.getByText('9')).toBeInTheDocument())
    expect(screen.queryByText(/best 9 days/)).not.toBeInTheDocument()
  })
})

describe('the topic tiles', () => {
  // The check that used to live here parsed `TOPICS` and `ICONS` out of
  // `Dashboard.jsx`, because they were literals in it and disagreed. They are
  // not any more -- both come from `lib/topics.js`, which is the only place a
  // topic list is written down -- so this parsed nothing and reported itself
  // inert, which is the guard it carried for exactly that.
  //
  // `lib/topics.test.js` replaces it and is strictly stronger: it checks every
  // topic has an icon at the source, and additionally that the list matches
  // the backend's, which no per-page check could.
  it('take their icons from the shared list', async () => {
    const { TOPICS, TOPIC_ICONS } = await import('../../lib/topics')
    const source = readFileSync(
      resolve(process.cwd(), 'src/pages/student/Dashboard.jsx'), 'utf8')
    expect(source).toContain("from '../../lib/topics'")
    expect(TOPICS.filter(t => !TOPIC_ICONS[t])).toEqual([])
  })
})
