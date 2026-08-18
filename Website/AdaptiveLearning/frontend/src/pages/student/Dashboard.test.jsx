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

// Its own network call, and not what these tests are about.
vi.mock('../../components/consent/ParentRestoredBanner', () => ({ default: () => null }))
// Both banners fetch on mount and both have their own file. Unmocked, this
// page's route table has no entry for them, and the `noRoute` throw would be
// swallowed by the banner's own catch -- so the test would pass with an error
// nobody sees.
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
    // Roughly one boy in twelve cannot separate the red from the green, and
    // this is a page for children. The percentage is the signal; the tint only
    // makes it scannable.
    draw()

    expect(await screen.findByText('30%')).toBeInTheDocument()
    expect(screen.getByText('90%')).toBeInTheDocument()
  })

  it('leaves a topic with no attempts plain, saying nothing about it', async () => {
    draw()
    await screen.findByText('30%')

    // `mode` was not in the payload at all. It should render as a topic in the
    // curriculum and carry no figure.
    expect(screen.getByText('mode')).toBeInTheDocument()
    expect(screen.queryByText('0%')).not.toBeInTheDocument()
  })

  it('draws the same plain tiles when the breakdown could not be read', async () => {
    // The reason `topics` may fail to `{}`: an unmeasured tile and a tile whose
    // read failed look identical, so neither asserts anything and neither can
    // be wrong. If this ever gains copy, the failure needs its own flag first.
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
    // One unlucky question is 0%. Ranking on that would tell a child their
    // weakest subject is one they have barely met -- wrong, and discouraging.
    overrideApi(BREAKDOWN, () => ([
      topic('algebra', 0, 1),
      topic('geometry', 70, 10),
    ]))

    draw()

    // Geometry is the only rankable topic, so it wins by default rather than
    // algebra winning on a single miss.
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
    // `best_streak` was in the payload all along and rendered nowhere, so a
    // student on a bad week saw only the number they had fallen to.
    draw()
    expect(await screen.findByText('best 9 days')).toBeInTheDocument()
  })

  it('does not repeat the number back while the student is on their best run', async () => {
    // The two are equal then, and "best 6" under a 6 reads as though something
    // is missing rather than as an achievement.
    overrideApi('/api/stats/me', () => ({ ...STATS, current_streak: 9, best_streak: 9 }))

    draw()

    await waitFor(() => expect(screen.getByText('9')).toBeInTheDocument())
    expect(screen.queryByText(/best 9 days/)).not.toBeInTheDocument()
  })
})
