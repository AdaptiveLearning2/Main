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
