import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { vi } from 'vitest'
import SessionReview from './SessionReview'

vi.mock('../../lib/api', () => ({ apiFetch: vi.fn() }))

const { apiFetch } = await import('../../lib/api')

const SESSION_ID = 'session-1'

function renderAt(id = SESSION_ID) {
  return render(
    <MemoryRouter initialEntries={[`/teacher/sessions/${id}/review`]}>
      <Routes>
        <Route path="/teacher/sessions/:sessionId/review" element={<SessionReview />} />
      </Routes>
    </MemoryRouter>,
  )
}

beforeEach(() => {
  apiFetch.mockReset()
})

// jsdom can't measure layout, so recharts renders its charts at 0x0 with no
// internals (no legend text, sectors, or series names). These tests can only
// check the page's own JSX text, not chart contents.
describe('the two stress figures', () => {
  it('titles the heart-derived pie distinctly, never bare "Stress"', async () => {
    // EEG-derived stress and heart-derived stress are different measurements
    // and must never share a "Stress" label. Only the pie's heading is
    // checked here since chart internals aren't reachable (see note above).
    apiFetch.mockResolvedValue({
      cognitive: [
        { ts: '2026-08-10T09:00:00Z', focus: 0.6, engagement: 0.5, stress: 0.4 },
        { ts: '2026-08-10T09:01:00Z', focus: 0.6, engagement: 0.5, stress: 0.4 },
      ],
      face: [],
      heart: [{ ts: '2026-08-10T09:00:30Z', stress_category: 'low' }],
      answers: [],
    })
    renderAt()

    await waitFor(() => expect(screen.getByText('Heart-rate stress')).toBeInTheDocument())
    expect(screen.queryByText('Stress')).not.toBeInTheDocument()
  })
})

// ── the archived-chart fallback ─────────────────────────────────────────────
//
// Per-sample rows expire, but archived chart SVGs don't, so past expiry the
// archive is the only remaining view of a session.

const EXPIRED = { cognitive: [], face: [], heart: [], answers: [] }

function mockPair(charts) {
  apiFetch.mockImplementation((url) =>
    Promise.resolve(url.endsWith('/charts') ? charts : EXPIRED))
}

describe('the archived-chart fallback', () => {
  it('asks for the archive only when every channel is empty', async () => {
    // A session with data shouldn't pay for the extra archive fetch.
    apiFetch.mockResolvedValue({
      cognitive: [
        { ts: '2026-08-10T09:00:00Z', focus: 0.6 },
        { ts: '2026-08-10T09:01:00Z', focus: 0.7 },
      ],
      face: [], heart: [], answers: [],
    })
    renderAt()

    await waitFor(() => expect(apiFetch).toHaveBeenCalled())
    expect(apiFetch.mock.calls.some(c => String(c[0]).endsWith('/charts'))).toBe(false)
  })

  it('draws the archived charts once the rows have expired', async () => {
    mockPair({
      archived: true,
      charts: {
        cognitive_timeline: 'https://storage.test/a/cognitive_timeline.svg',
        heart_rate: 'https://storage.test/a/heart_rate.svg',
        emotion_pie: 'https://storage.test/a/emotion_pie.svg',
        stress_pie: null,
      },
      unavailable: [],
    })
    renderAt()

    await waitFor(() =>
      expect(screen.getByAltText('Cognitive timeline')).toBeInTheDocument())
    expect(screen.getByAltText('Heart rate and HRV')).toBeInTheDocument()
    expect(screen.getByAltText('Emotion mix')).toBeInTheDocument()
    // Null means that channel drew nothing, so no image should render for it.
    expect(screen.queryByAltText('Autonomic arousal')).not.toBeInTheDocument()
    expect(screen.getByText(/per-sample rows for this session have expired/i))
      .toBeInTheDocument()
  })

  it('does not tell a teacher to wait for a stream on an expired session', async () => {
    mockPair({
      archived: true,
      charts: { cognitive_timeline: 'https://storage.test/a/cognitive_timeline.svg' },
      unavailable: [],
    })
    renderAt()

    await waitFor(() =>
      expect(screen.getByAltText('Cognitive timeline')).toBeInTheDocument())
    expect(screen.queryByText(/once a sensor starts streaming/i))
      .not.toBeInTheDocument()
  })

  it('says nothing was recorded when the archive ran and drew nothing', async () => {
    // Distinct from the case below: the archive ran here, so we know for sure.
    mockPair({
      archived: true,
      charts: { cognitive_timeline: null, heart_rate: null, emotion_pie: null, stress_pie: null },
      unavailable: [],
    })
    renderAt()

    await waitFor(() =>
      expect(screen.getByText(/nothing was recorded on this channel/i))
        .toBeInTheDocument())
  })

  it('reports an unreadable object as a fault, not as an absence', async () => {
    // A path was recorded but the object couldn't be read -- a fault, not
    // proof nothing was recorded.
    mockPair({ archived: true, charts: {}, unavailable: ['cognitive_timeline'] })
    renderAt()

    await waitFor(() =>
      expect(screen.getByText(/archived chart for this session could not be loaded/i))
        .toBeInTheDocument())
  })

  it('falls back to the old wording when the archive never ran', async () => {
    mockPair({ archived: false, charts: {}, unavailable: [] })
    renderAt()

    await waitFor(() =>
      expect(screen.getByText(/no signal samples for this session/i))
        .toBeInTheDocument())
  })

  it('survives the archive call failing without blanking the page', async () => {
    // The rest of the page doesn't depend on the archive, so a failed
    // archive fetch must not blank the whole page.
    apiFetch.mockImplementation((url) =>
      String(url).endsWith('/charts')
        ? Promise.reject(new Error('signing failed'))
        : Promise.resolve({ ...EXPIRED, answers: [{ correct: true }] }))
    renderAt()

    await waitFor(() => expect(screen.getByText('Session Review')).toBeInTheDocument())
    expect(screen.queryByText(/could not load session/i)).not.toBeInTheDocument()
    // A failed fetch must say so, not read as "no signal samples recorded".
    expect(screen.getByText(/archived charts could not be loaded/i)).toBeInTheDocument()
    expect(screen.queryByText(/no signal samples for this session/i)).not.toBeInTheDocument()
  })

  it('still says a pre-archive session recorded nothing', async () => {
    // The mirror case: a real "no data" must stay distinct from a failed fetch.
    apiFetch.mockImplementation((url) =>
      String(url).endsWith('/charts')
        ? Promise.resolve({ archived: false, charts: {}, unavailable: [] })
        : Promise.resolve({ ...EXPIRED, answers: [{ correct: true }] }))
    renderAt()

    await waitFor(() => expect(screen.getByText('Session Review')).toBeInTheDocument())
    expect(screen.getByText(/no signal samples for this session/i)).toBeInTheDocument()
    expect(screen.queryByText(/could not be loaded/i)).not.toBeInTheDocument()
  })
})

// ── mixed states across a section's charts ──────────────────────────────────
//
// Each chart is signed independently and can fail on its own, so one section
// can have a mix of drawn, empty, and unreadable charts at once.

describe('a fault is never reported as an absence', () => {
  it('does not call an unreadable heart chart "nothing recorded"', async () => {
    // cognitive drew nothing (real absence), heart_rate couldn't be read
    // (a fault). Must not report the fault as an absence too.
    mockPair({
      archived: true,
      charts: { cognitive_timeline: null, emotion_pie: null, stress_pie: null },
      unavailable: ['heart_rate'],
    })
    renderAt()

    await waitFor(() =>
      expect(screen.getByText(/archived chart for this session could not be loaded/i))
        .toBeInTheDocument())
    expect(screen.queryByText(/nothing was recorded on this channel/i))
      .not.toBeInTheDocument()
  })

  it('flags the fault even when the other chart in the section drew fine', async () => {
    // One chart rendered, one unreadable -- both must be reported.
    mockPair({
      archived: true,
      charts: { cognitive_timeline: 'https://storage.test/a/cognitive_timeline.svg' },
      unavailable: ['heart_rate'],
    })
    renderAt()

    await waitFor(() =>
      expect(screen.getByAltText('Cognitive timeline')).toBeInTheDocument())
    expect(screen.getByText(/one archived chart for this session could not be loaded/i))
      .toBeInTheDocument()
  })

  it('does not call an unreadable emotion chart "no face samples"', async () => {
    mockPair({ archived: true, charts: { cognitive_timeline: null }, unavailable: ['emotion_pie'] })
    renderAt()

    await waitFor(() => expect(screen.getByText('Emotion timeline')).toBeInTheDocument())
    expect(screen.queryByText(/no face samples for this session/i))
      .not.toBeInTheDocument()
  })

  it('keeps the pie section mounted so an unreadable pie is still reported', async () => {
    // Section must stay mounted so an unreadable pie chart still gets reported.
    mockPair({ archived: true, charts: { emotion_pie: null }, unavailable: ['stress_pie'] })
    renderAt()

    await waitFor(() => expect(screen.getByText('Heart-rate stress')).toBeInTheDocument())
    expect(screen.getAllByText(/could not be loaded/i).length).toBeGreaterThan(0)
  })
})
