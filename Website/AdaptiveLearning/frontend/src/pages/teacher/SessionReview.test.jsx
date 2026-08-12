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

// Coverage note: this page's charts sit inside recharts' ResponsiveContainer,
// which measures its real size from layout -- something jsdom does not
// implement (src/test/setup.js filters the resulting -1x-1 warning rather
// than fix it, since "the charts are fine in a browser"). With a 0x0
// container recharts renders no Line/Pie internals at all -- no legend text,
// no sectors, no series names -- so the heart-merge nearest-neighbour fix,
// the EMOTION_COLOURS key fix, and the failover-marker axis-gating fix are
// not exercisable through a render test here; they were verified by reading
// the code and tracing the data through by hand. What *is* verifiable
// without chart internals is the page's own JSX text, which is what this
// file covers.
describe('the two stress figures', () => {
  it('titles the heart-derived pie distinctly, never bare "Stress"', async () => {
    // CLAUDE.md: cognitive_signals.stress (EEG, inverted calm) and
    // heart_signals.stress_category (physiological, heart-derived) must never
    // share a "Stress" label -- they can disagree, and a shared label reads
    // as a contradiction in one measurement rather than two different
    // signals. This only covers the pie's own heading, which renders outside
    // the 0x0 chart area; the timeline's "EEG stress" legend entry is not
    // reachable from this test environment (see file-level note above).
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
// `expire_signal_rows` deletes per-sample rows on `ends_on` and deliberately
// leaves the archived SVGs, so past that date the archive is the *only*
// remaining view of a session. Without this fallback the page renders "no
// samples" over charts sitting in the bucket -- an absence contradicted by
// the data, which is the failure the whole reporting layer is built around.

const EXPIRED = { cognitive: [], face: [], heart: [], answers: [] }

function mockPair(charts) {
  apiFetch.mockImplementation((url) =>
    Promise.resolve(url.endsWith('/charts') ? charts : EXPIRED))
}

describe('the archived-chart fallback', () => {
  it('asks for the archive only when every channel is empty', async () => {
    // A session that still has rows must not pay for the extra round trip,
    // and must not sign four URLs nobody will look at.
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
    // Null means that channel drew nothing, so there is no picture to show
    // and inventing an empty one would assert the session had it and it read
    // flat.
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
    // Distinct from the case below: here we know, because the archive ran.
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
    // A path was recorded and the object could not be read. Saying "nothing
    // was recorded" here would be a claim the read never established -- a
    // bucket half-emptied by hand would otherwise read as a term nobody wore
    // a headband in.
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
    // The answers, tiles and accuracy do not depend on the archive. Letting a
    // missing picture become the page's error state would be the opposite of
    // what this fallback is for.
    apiFetch.mockImplementation((url) =>
      String(url).endsWith('/charts')
        ? Promise.reject(new Error('signing failed'))
        : Promise.resolve({ ...EXPIRED, answers: [{ correct: true }] }))
    renderAt()

    await waitFor(() => expect(screen.getByText('Session Review')).toBeInTheDocument())
    expect(screen.queryByText(/could not load session/i)).not.toBeInTheDocument()
    expect(screen.getByText(/no signal samples for this session/i)).toBeInTheDocument()
  })
})

// ── mixed states across a section's charts ──────────────────────────────────
//
// The backend signs each of the four objects independently and puts a name in
// `unavailable` on that object's own read failure, so states genuinely differ
// within one section. Every test above uses a uniform state, which is exactly
// why the first version of this page read one channel and spoke for the set.

describe('a fault is never reported as an absence', () => {
  it('does not call an unreadable heart chart "nothing recorded"', async () => {
    // cognitive drew nothing (a fact about the session) while heart_rate's
    // object could not be read (a fault). Reading only cognitive_timeline
    // downgrades the fault to an absence.
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
    // One chart rendered, one unreadable. Silence here presents a partial
    // view as the whole session.
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
    // Hiding the section reports nothing at all -- the absence swallowing the
    // fault one level up.
    mockPair({ archived: true, charts: { emotion_pie: null }, unavailable: ['stress_pie'] })
    renderAt()

    await waitFor(() => expect(screen.getByText('Heart-rate stress')).toBeInTheDocument())
    expect(screen.getAllByText(/could not be loaded/i).length).toBeGreaterThan(0)
  })
})
