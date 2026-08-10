import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { vi } from 'vitest'
import StudentProgressReport from './StudentProgressReport'

// The viewer-side facial opt-out is gone: stored consent decides what the
// server reads, and the teacher's remaining switch only hides what is drawn.
// So what matters on the wire now is the *absence* of a viewer flag, and what
// matters on screen is that hiding costs nothing and changes no request.

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

// The default responses, named so a test that overrides one endpoint can defer
// the rest here rather than restating all four.
function defaultFetch(url) {
  const u = String(url)
  if (u.includes('/stats/'))                return Promise.resolve({ total_questions: 4, total_correct: 2, current_streak: 1 })
  if (u.includes('/weekly-report'))         return Promise.resolve(emptyReport)
  if (u.includes('/learning-strategies'))   return Promise.resolve({ strategies: ['Review fractions'], source: 'rule-based' })
  return Promise.resolve([]) // sessions + performance
}

beforeEach(() => {
  localStorage.clear()
  apiFetch.mockReset()
  apiFetch.mockImplementation(defaultFetch)
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






describe('at-home strategies', () => {
  it('is absent unless the route asks for it', async () => {
    // Teacher-facing copy would be wrong: these are written for a parent
    // sitting down with a child at home.
    renderReport()
    await screen.findByText('Recent Sessions')
    expect(screen.queryByText(/at-home learning strategies/i)).not.toBeInTheDocument()
  })


  it('carries the "signals did not load" flag from the response to the panel', async () => {
    // The endpoint answers with a usable generic list when its aggregate
    // fails, so nothing about the response says the advice is not about this
    // student's week except basis.signals_retrieved. Dropping it on the way
    // through left the panel claiming the list was built from the report.
    apiFetch.mockImplementation((u) => {
      if (String(u).includes('/learning-strategies')) {
        return Promise.resolve({
          strategies: ['Review fractions'],
          source: 'rule-based',
          basis: { signals_retrieved: false },
        })
      }
      return defaultFetch(u)
    })

    renderReport({ showStrategies: true })
    await screen.findByText('Recent Sessions')
    await userEvent.click(screen.getByRole('button', { name: /generate strategies/i }))

    await screen.findByText('Review fractions')
    expect(screen.getByText(/so these are general suggestions/i)).toBeInTheDocument()
    expect(screen.queryByText(/built from this week's report/i)).not.toBeInTheDocument()
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



it('asks for the report without a viewer-side flag', async () => {
  // The old control sent `include_face`, so the client could narrow what the
  // server read. Consent decides that now, server-side, and it is not the
  // viewer's to override -- a second axis on the same question is what made
  // the old control expensive to reason about.
  renderReport()

  await waitFor(() => expect(urlsFor('/weekly-report')).toHaveLength(1))
  expect(apiFetch.mock.calls.map(c => String(c[0])).join(' ')).not.toMatch(/include_face/)
})

it('renders the sensor panels by default', async () => {
  // The teacher's filter is passed in; a caller that knows nothing about it --
  // the parent surface -- must get the whole report.
  renderReport()

  expect(await screen.findByText(/Weekly EEG/)).toBeInTheDocument()
})

it('hides the sensor panels when the caller asks, without changing the request', async () => {
  // Client-side only, which is the whole point of keeping it out of the API.
  // See lib/viewPrefs.js for why a filter that fetches what it hides is
  // acceptable here and would not be for consent.
  renderReport({ showSignals: false })

  // Still fetched -- the request is unchanged, only the rendering is not.
  await waitFor(() => expect(urlsFor('/weekly-report')).toHaveLength(1))
  expect(screen.queryByText(/Weekly EEG/)).not.toBeInTheDocument()
})
