import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { vi } from 'vitest'
import StudentProgressReport from './StudentProgressReport'

// Stored consent decides what the server reads; the teacher's switch only
// hides what is drawn. On the wire that means no viewer flag; on screen it
// means hiding costs nothing and changes no request.

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

// The default responses, so a test overriding one endpoint can defer the
// rest here rather than restating all four.
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
  // Guards against two effects fetching the report on mount.
  renderReport()
  await screen.findByText('Recent Sessions')
  expect(urlsFor('/weekly-report')).toHaveLength(1)
})






describe('at-home strategies', () => {
  it('is absent unless the route asks for it', async () => {
    // These are written for a parent at home; teacher-facing copy is wrong here.
    renderReport()
    await screen.findByText('Recent Sessions')
    expect(screen.queryByText(/at-home learning strategies/i)).not.toBeInTheDocument()
  })


  it('carries the "signals did not load" flag from the response to the panel', async () => {
    // `basis.signals_retrieved` is the only signal that the advice is generic
    // rather than built from this student's week -- dropping it on the way
    // through would make the panel misclaim it.
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



  it('POSTs a JSON body, not an empty request', async () => {
    // FastAPI requires a body even though every field defaults -- a bodyless
    // POST 422s. apiFetch is mocked, so this only asserts the call carries a
    // body for lib/api.js's `if (body)` to serialize.
    renderReport({ showStrategies: true })
    await screen.findByText('Recent Sessions')
    await userEvent.click(screen.getByRole('button', { name: /generate strategies/i }))

    await waitFor(() => expect(urlsFor('/learning-strategies')).toHaveLength(1))
    const [, opts] = apiFetch.mock.calls.find(c => String(c[0]).includes('/learning-strategies'))
    expect(opts.body).toBeTruthy()
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
  // Consent decides what the server reads -- not a client-side `include_face`
  // flag.
  renderReport()

  await waitFor(() => expect(urlsFor('/weekly-report')).toHaveLength(1))
  expect(apiFetch.mock.calls.map(c => String(c[0])).join(' ')).not.toMatch(/include_face/)
})

it('renders the sensor panels by default', async () => {
  // The teacher's filter is passed in; the parent surface, which knows
  // nothing about it, must get the whole report.
  renderReport()

  expect(await screen.findByText(/Weekly EEG/)).toBeInTheDocument()
})

it('hides the sensor panels when the caller asks, without changing the request', async () => {
  // Client-side only. See lib/viewPrefs.js for why a filter that fetches
  // what it hides is acceptable here but not for consent.
  renderReport({ showSignals: false })

  // Still fetched -- the request is unchanged; only rendering is.
  await waitFor(() => expect(urlsFor('/weekly-report')).toHaveLength(1))
  expect(screen.queryByText(/Weekly EEG/)).not.toBeInTheDocument()
})
