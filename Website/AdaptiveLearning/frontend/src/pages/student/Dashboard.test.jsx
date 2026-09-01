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
  // A source check, because the defect is invisible at runtime: React renders
  // `undefined` as nothing, so a topic with no icon is an empty slot rather
  // than an error. `TOPICS` and `ICONS` are two literals one line apart, and
  // an edit adding `missing_number` and `patterns` to the first and not the
  // second landed exactly that way -- on the panel a student sees, for the two
  // topics aimed at the youngest users.
  //
  // The other three topic lists in this app all default a missing icon; this
  // one did not, which is why half an edit showed nowhere else.
  const source = readFileSync(
    resolve(process.cwd(), 'src/pages/student/Dashboard.jsx'), 'utf8')

  const listOf = (name) => {
    const line = source.split('\n').find(l => l.startsWith(`const ${name}`))
    return [...line.matchAll(/([a-z_]+)\s*:/g)].map(m => m[1])
  }

  it('gives every topic an icon', () => {
    const topics = [...source.split('\n')
      .find(l => l.startsWith('const TOPICS'))
      .matchAll(/'([a-z_]+)'/g)].map(m => m[1])
    const icons = listOf('ICONS')
    expect(topics.length).toBeGreaterThan(0)
    expect(icons.length).toBeGreaterThan(0)
    expect(topics.filter(t => !icons.includes(t))).toEqual([])
  })
})
