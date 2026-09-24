import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { vi } from 'vitest'
import StudentProgressReport from './StudentProgressReport'

// Consent decides what the server reads; the teacher's switch only hides what is drawn.

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

// The trend reads a different table, so it has its own empty fixture.
const emptyTrend = { weeks: [], retrieved: true, heart_included: false,
                     emotion_included: true }

// Default responses; a test overriding one endpoint defers the rest here.
function defaultFetch(url) {
  const u = String(url)
  if (u.includes('/stats/'))                return Promise.resolve({ total_questions: 4, total_correct: 2, current_streak: 1 })
  if (u.includes('/weekly-report'))         return Promise.resolve(emptyReport)
  if (u.includes('/signal-trend'))          return Promise.resolve(emptyTrend)
  if (u.includes('/learning-strategies'))   return Promise.resolve({ strategies: ['Review fractions'], source: 'rule-based' })
  if (u.includes('/chart-summary'))         return Promise.resolve({ summary: ['Focus is 63%.'], source: 'rule-based', basis: {} })
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
  renderReport()
  await screen.findByText('Recent Sessions')
  expect(urlsFor('/weekly-report')).toHaveLength(1)
})






describe('at-home strategies', () => {
  it('is absent unless the route asks for it', async () => {
    renderReport()
    await screen.findByText('Recent Sessions')
    expect(screen.queryByText(/at-home learning strategies/i)).not.toBeInTheDocument()
  })


  it('carries the "signals did not load" flag from the response to the panel', async () => {
    // `basis.signals_retrieved` is the only marker that the advice is generic.
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



  it('frames the panel for whoever is reading the report', async () => {
    renderReport({ showStrategies: true, viewerRole: 'teacher' })
    await screen.findByText('Recent Sessions')
    expect(screen.getByText(/written for a family to use at home/i)).toBeInTheDocument()
  })

  it('POSTs a JSON body, not an empty request', async () => {
    // FastAPI 422s a bodyless POST even though every field defaults.
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
  renderReport()

  await waitFor(() => expect(urlsFor('/weekly-report')).toHaveLength(1))
  expect(apiFetch.mock.calls.map(c => String(c[0])).join(' ')).not.toMatch(/include_face/)
})

it('renders the sensor panels by default', async () => {
  // The parent surface passes no filter and gets the whole report.
  renderReport()

  expect(await screen.findByText(/Weekly EEG/)).toBeInTheDocument()
})

it('hides the sensor panels when the caller asks, without changing the request', async () => {
  // Client-side only; see lib/viewPrefs.js.
  renderReport({ showSignals: false })

  await waitFor(() => expect(urlsFor('/weekly-report')).toHaveLength(1))
  expect(screen.queryByText(/Weekly EEG/)).not.toBeInTheDocument()
})


it('a failed trend does not blank the weekly report, or the other way round', async () => {
  // Two endpoints over two tables, so two error states.
  apiFetch.mockImplementation(url => {
    const u = String(url)
    if (u.includes('/signal-trend')) return Promise.reject(new Error('down'))
    return defaultFetch(u)
  })

  renderReport()

  expect(await screen.findByText(/term trend could not be loaded/i)).toBeInTheDocument()
  expect(screen.getByText(/Weekly EEG & Face Report/i)).toBeInTheDocument()
})

describe('chart-explaining summary', () => {
  it('is absent unless the route asks for it', async () => {
    renderReport()
    await screen.findByText('Recent Sessions')
    expect(screen.queryByRole('button', { name: /generate summary/i })).not.toBeInTheDocument()
  })

  it('fetches nothing until the button is pressed', async () => {
    // An auto-fetch would spend a model call per report page.
    renderReport({ showChartSummary: true })
    await screen.findByText('Recent Sessions')
    expect(urlsFor('/chart-summary')).toHaveLength(0)

    await userEvent.click(screen.getByRole('button', { name: /generate summary/i }))
    await screen.findByText('Focus is 63%.')
    expect(urlsFor('/chart-summary')).toHaveLength(1)
  })

  it('POSTs a JSON body, not an empty request', async () => {
    // FastAPI 422s a bodyless POST even though every field defaults.
    renderReport({ showChartSummary: true })
    await screen.findByText('Recent Sessions')
    await userEvent.click(screen.getByRole('button', { name: /generate summary/i }))

    await waitFor(() => expect(urlsFor('/chart-summary')).toHaveLength(1))
    const [, opts] = apiFetch.mock.calls.find(c => String(c[0]).includes('/chart-summary'))
    expect(opts.body).toBeTruthy()
  })

  it('names which part of the report failed to load, not just that something did', async () => {
    // Several reads sit behind one response, each with its own flag.
    apiFetch.mockImplementation((u) => {
      if (String(u).includes('/chart-summary')) {
        return Promise.resolve({
          summary: ['Focus is 63%.'],
          source: 'rule-based',
          basis: { signals_retrieved: true, trend_retrieved: false, stats_retrieved: true },
        })
      }
      return defaultFetch(u)
    })

    renderReport({ showChartSummary: true })
    await screen.findByText('Recent Sessions')
    await userEvent.click(screen.getByRole('button', { name: /generate summary/i }))

    await screen.findByText('Focus is 63%.')
    expect(screen.getByText(/the term trend/i)).toBeInTheDocument()
    expect(screen.queryByText(/the practice totals/i)).not.toBeInTheDocument()
    expect(screen.queryByText(/the topic figures/i)).not.toBeInTheDocument()
  })

  it('names a failed topics read, which is the fourth of the four', async () => {
    // `_topic_breakdown` swallows failure into [], which here would become a claim.
    apiFetch.mockImplementation((u) => {
      if (String(u).includes('/chart-summary')) {
        return Promise.resolve({
          summary: ['Focus is 63%.'],
          source: 'rule-based',
          basis: { signals_retrieved: true, trend_retrieved: true,
                   stats_retrieved: true, topics_retrieved: false },
        })
      }
      return defaultFetch(u)
    })

    renderReport({ showChartSummary: true })
    await screen.findByText('Recent Sessions')
    await userEvent.click(screen.getByRole('button', { name: /generate summary/i }))

    await screen.findByText('Focus is 63%.')
    expect(screen.getByText(/the topic figures/i)).toBeInTheDocument()
    expect(screen.queryByText(/the term trend/i)).not.toBeInTheDocument()
  })

  it('does not claim an outage for a payload that predates the flags', async () => {
    // Absent is not false.
    renderReport({ showChartSummary: true })
    await screen.findByText('Recent Sessions')
    await userEvent.click(screen.getByRole('button', { name: /generate summary/i }))

    await screen.findByText('Focus is 63%.')
    expect(screen.queryByText(/couldn’t be loaded/i)).not.toBeInTheDocument()
  })

  it('keeps its own state, so the two panels cannot overwrite each other', async () => {
    // Both press orders, so neither handler can clear the other's panel.
    renderReport({ showChartSummary: true, showStrategies: true })
    await screen.findByText('Recent Sessions')

    await userEvent.click(screen.getByRole('button', { name: /generate summary/i }))
    await screen.findByText('Focus is 63%.')
    await userEvent.click(screen.getByRole('button', { name: /generate strategies/i }))
    await screen.findByText('Review fractions')
    expect(screen.getByText('Focus is 63%.')).toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: /generate summary/i }))
    await waitFor(() => expect(urlsFor('/chart-summary')).toHaveLength(2))
    expect(screen.getByText('Review fractions')).toBeInTheDocument()
  })

  it('surfaces a failure instead of silently showing nothing', async () => {
    apiFetch.mockImplementation((u) => {
      if (String(u).includes('/chart-summary')) return Promise.reject(new Error('Backend unavailable'))
      return defaultFetch(u)
    })
    renderReport({ showChartSummary: true })
    await screen.findByText('Recent Sessions')
    await userEvent.click(screen.getByRole('button', { name: /generate summary/i }))
    expect(await screen.findByText('Backend unavailable')).toBeInTheDocument()
  })
})
