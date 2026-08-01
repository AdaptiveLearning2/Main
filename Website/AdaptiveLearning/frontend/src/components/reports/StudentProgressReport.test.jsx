import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { vi } from 'vitest'
import StudentProgressReport from './StudentProgressReport'

// The facial-recognition opt-out is a privacy control, so what it causes on
// the wire matters as much as what it renders: the report has to be re-fetched
// with the new flag, the choice has to survive a reload, and flipping it must
// not drag the unrelated academic endpoints along with it.

vi.mock('../../lib/api', () => ({ apiFetch: vi.fn() }))

const { apiFetch } = await import('../../lib/api')

const SID = 'stu-1'

const emptyReport = {
  days: 7,
  face_included: true,
  averages: {}, highlights: {}, sample_counts: {}, latest: {}, daily: [],
  summary: 'No EEG or facial recognition samples were recorded this week.',
}

function urlsFor(fragment) {
  return apiFetch.mock.calls.map(c => String(c[0])).filter(u => u.includes(fragment))
}

beforeEach(() => {
  localStorage.clear()
  apiFetch.mockReset()
  apiFetch.mockImplementation((url) => {
    const u = String(url)
    if (u.includes('/stats/'))                return Promise.resolve({ total_questions: 4, total_correct: 2, current_streak: 1 })
    if (u.includes('/weekly-report'))         return Promise.resolve(emptyReport)
    if (u.includes('/learning-strategies'))   return Promise.resolve({ strategies: ['Review fractions'], source: 'rule-based' })
    return Promise.resolve([]) // sessions + performance
  })
})

function renderReport(props = {}) {
  return render(
    <MemoryRouter>
      <StudentProgressReport studentId={SID} backTo="/parent" backLabel="Back" {...props} />
    </MemoryRouter>,
  )
}

it('requests the weekly report once per render, not twice', async () => {
  // Two effects both fetching the report on mount is the duplicate-request bug
  // in #36; one of them also read includeFace without declaring it.
  renderReport()
  await screen.findByText('Recent Sessions')
  expect(urlsFor('/weekly-report')).toHaveLength(1)
})

it('defaults to including facial data', async () => {
  renderReport()
  await screen.findByText('Recent Sessions')
  expect(urlsFor('/weekly-report')[0]).toContain('include_face=true')
})

it('re-fetches only the signal report when the toggle flips', async () => {
  renderReport()
  await screen.findByText('Recent Sessions')
  const academicBefore = urlsFor('/stats/').length

  await userEvent.click(screen.getByRole('switch'))

  await waitFor(() => expect(urlsFor('/weekly-report')).toHaveLength(2))
  expect(urlsFor('/weekly-report')[1]).toContain('include_face=false')
  // The academic endpoints do not depend on the flag and must not be re-run.
  expect(urlsFor('/stats/')).toHaveLength(academicBefore)
})

it('remembers the choice across mounts', async () => {
  const { unmount } = renderReport()
  await screen.findByText('Recent Sessions')
  await userEvent.click(screen.getByRole('switch'))
  await waitFor(() => expect(urlsFor('/weekly-report')).toHaveLength(2))
  unmount()

  apiFetch.mockClear()
  renderReport()
  await screen.findByText('Recent Sessions')
  expect(urlsFor('/weekly-report')[0]).toContain('include_face=false')
})

it('survives localStorage being unavailable', async () => {
  // Safari private mode throws on setItem. The toggle should still work for
  // the session rather than taking the page down.
  const spy = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
    throw new Error('QuotaExceededError')
  })
  renderReport()
  await screen.findByText('Recent Sessions')
  await userEvent.click(screen.getByRole('switch'))
  await waitFor(() => expect(urlsFor('/weekly-report')).toHaveLength(2))
  spy.mockRestore()
})

describe('at-home strategies', () => {
  it('is absent unless the route asks for it', async () => {
    // Teacher-facing copy would be wrong: these are written for a parent
    // sitting down with a child at home.
    renderReport()
    await screen.findByText('Recent Sessions')
    expect(screen.queryByText(/at-home learning strategies/i)).not.toBeInTheDocument()
  })

  it('posts the current facial setting and renders what comes back', async () => {
    renderReport({ showStrategies: true })
    await screen.findByText('Recent Sessions')
    await userEvent.click(screen.getByRole('button', { name: /generate strategies/i }))

    await screen.findByText('Review fractions')
    expect(screen.getByText('Source: rule-based')).toBeInTheDocument()
    const call = apiFetch.mock.calls.find(c => String(c[0]).includes('/learning-strategies'))
    expect(call[1]).toMatchObject({ method: 'POST', body: { include_face: true } })
  })

  it('drops advice built from data the new setting excludes', async () => {
    renderReport({ showStrategies: true })
    await screen.findByText('Recent Sessions')
    await userEvent.click(screen.getByRole('button', { name: /generate strategies/i }))
    await screen.findByText('Review fractions')

    await userEvent.click(screen.getByRole('switch'))
    // Leaving it on screen would present advice derived from facial data the
    // viewer has just asked to exclude.
    await waitFor(() => expect(screen.queryByText('Review fractions')).not.toBeInTheDocument())
  })

  it('surfaces a failure instead of silently showing nothing', async () => {
    apiFetch.mockImplementation((url) => {
      const u = String(url)
      if (u.includes('/learning-strategies')) return Promise.reject(new Error('Backend unavailable'))
      if (u.includes('/stats/'))              return Promise.resolve({ total_questions: 0, total_correct: 0, current_streak: 0 })
      if (u.includes('/weekly-report'))       return Promise.resolve(emptyReport)
      return Promise.resolve([])
    })
    renderReport({ showStrategies: true })
    await screen.findByText('Recent Sessions')
    await userEvent.click(screen.getByRole('button', { name: /generate strategies/i }))
    expect(await screen.findByText('Backend unavailable')).toBeInTheDocument()
  })
})
