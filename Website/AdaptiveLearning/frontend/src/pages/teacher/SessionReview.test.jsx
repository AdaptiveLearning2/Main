import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
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

// jsdom renders recharts at 0x0 with no internals, so only the page's own JSX text is checkable.
describe('the two stress figures', () => {
  it('titles the heart-derived pie distinctly, never bare "Stress"', async () => {
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
// Past row expiry, the archived SVGs are the only remaining view of a session.

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
    // Null means that channel drew nothing.
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
// Each chart is signed independently, so a section can mix drawn, empty and unreadable.

describe('a fault is never reported as an absence', () => {
  it('does not call an unreadable heart chart "nothing recorded"', async () => {
    // cognitive drew nothing (absence); heart_rate couldn't be read (fault).
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
    mockPair({ archived: true, charts: { emotion_pie: null }, unavailable: ['stress_pie'] })
    renderAt()

    await waitFor(() => expect(screen.getByText('Heart-rate stress')).toBeInTheDocument())
    expect(screen.getAllByText(/could not be loaded/i).length).toBeGreaterThan(0)
  })
})

// ─── the answers table ─────────────────────────────────────────────────────

describe('the answers table', () => {
  const QUESTION = {
    question_text: 'What is 7 x 6?',
    options: ['40', '42', '44'],
    correct_answer: '42',
    subject: 'algebra',
    difficulty: 'easy',
  }

  const answerRow = (over = {}) => ({
    answered_at: '2026-06-11T09:30:00Z', question_id: 'q-1',
    selected_index: 0, correct: false, questions: QUESTION, ...over,
  })

  function renderWith(answers) {
    // Resolved by URL, not call order: signals and charts are fetched in parallel.
    apiFetch.mockImplementation(url =>
      Promise.resolve(String(url).endsWith('/charts')
        ? { archived: false, charts: {} }
        : { cognitive: [], face: [], heart: [], answers }))
    return renderAt()
  }

  it('shows the topic rather than the question id', async () => {
    renderWith([answerRow()])
    expect(await screen.findByRole('button', { name: /algebra/ })).toBeInTheDocument()
    expect(screen.queryByText(/q-1/)).not.toBeInTheDocument()
  })

  it('shows the answer text rather than its index', async () => {
    renderWith([answerRow({ selected_index: 0 })])
    expect(await screen.findByText('40')).toBeInTheDocument()
  })

  it('marks which option was chosen and which was correct', async () => {
    const user = userEvent.setup()
    renderWith([answerRow({ selected_index: 0 })])
    await user.click(await screen.findByRole('button', { name: /algebra/ }))

    expect(screen.getByText('What is 7 x 6?')).toBeInTheDocument()
    // Spelled out, not left to colour alone.
    expect(screen.getByText('(chosen)')).toBeInTheDocument()
    expect(screen.getByText('(correct answer)')).toBeInTheDocument()
  })

  it('marks one option both chosen and correct when the answer was right', async () => {
    const user = userEvent.setup()
    renderWith([answerRow({ selected_index: 1, correct: true })])
    await user.click(await screen.findByRole('button', { name: /algebra/ }))
    expect(screen.getByText('(chosen)')).toBeInTheDocument()
    expect(screen.getByText('(correct answer)')).toBeInTheDocument()
  })

  it('collapses again on a second click', async () => {
    const user = userEvent.setup()
    renderWith([answerRow()])
    const toggle = await screen.findByRole('button', { name: /algebra/ })
    await user.click(toggle)
    expect(screen.getByText('What is 7 x 6?')).toBeInTheDocument()
    await user.click(toggle)
    expect(screen.queryByText('What is 7 x 6?')).not.toBeInTheDocument()
  })

  it('still shows an answer whose question has left the bank', async () => {
    // PostgREST left-joins the embed, so `questions: null` is a real shape.
    const user = userEvent.setup()
    renderWith([answerRow({ questions: null })])
    const toggle = await screen.findByRole('button', { name: /unknown topic/i })
    await user.click(toggle)
    expect(screen.getByText(/no longer in the question bank/i)).toBeInTheDocument()
  })

  it('falls back to the index when the option text cannot be resolved', async () => {
    // Better a bare number than a blank cell that reads as "no answer".
    renderWith([answerRow({ selected_index: 9 })])
    expect(await screen.findByText('Option 9')).toBeInTheDocument()
  })

  it('says so when the options were never recorded', async () => {
    const user = userEvent.setup()
    renderWith([answerRow({ questions: { ...QUESTION, options: null } })])
    await user.click(await screen.findByRole('button', { name: /algebra/ }))
    expect(screen.getByText(/options for this question were not recorded/i))
      .toBeInTheDocument()
  })

  it('resolves the correct option by value, not by position', async () => {
    // `questions.correct_answer` is text, not an index.
    const user = userEvent.setup()
    renderWith([answerRow({
      selected_index: 0,
      questions: { ...QUESTION, correct_answer: '44' },
    })])
    await user.click(await screen.findByRole('button', { name: /algebra/ }))
    const correct = screen.getByText('(correct answer)').closest('li')
    expect(correct).toHaveTextContent('44')
  })
})

describe('the correct-answer marker', () => {
  const answerRow = (over = {}) => ({
    answered_at: '2026-06-11T09:30:00Z', question_id: 'q-1',
    selected_index: 0, correct: false, ...over,
  })

  function renderWith(answers) {
    apiFetch.mockImplementation(url =>
      Promise.resolve(String(url).endsWith('/charts')
        ? { archived: false, charts: {} }
        : { cognitive: [], face: [], heart: [], answers }))
    return renderAt()
  }

  it('marks only one option when a distractor repeats the answer', async () => {
    // Not every generator dedupes its distractors.
    const user = userEvent.setup()
    renderWith([answerRow({
      questions: {
        question_text: 'Order these', options: ['42', '40', '42'],
        correct_answer: '42', subject: 'ordering', difficulty: 'easy',
      },
    })])
    await user.click(await screen.findByRole('button', { name: /ordering/ }))
    expect(screen.getAllByText('(correct answer)')).toHaveLength(1)
  })

  it('marks nothing when the answer is not among the options', async () => {
    // A wrong marker is worse than none.
    const user = userEvent.setup()
    renderWith([answerRow({
      questions: {
        question_text: 'What is 7 x 6?', options: ['40', '44'],
        correct_answer: '42', subject: 'algebra', difficulty: 'easy',
      },
    })])
    await user.click(await screen.findByRole('button', { name: /algebra/ }))
    expect(screen.queryByText('(correct answer)')).not.toBeInTheDocument()
    expect(screen.getByText('(chosen)')).toBeInTheDocument()
  })

  it('accepts a correct_answer that holds an index instead of a value', async () => {
    const user = userEvent.setup()
    renderWith([answerRow({
      questions: {
        question_text: 'Pick one', options: ['a', 'b', 'c'],
        correct_answer: '2', subject: 'algebra', difficulty: 'easy',
      },
    })])
    await user.click(await screen.findByRole('button', { name: /algebra/ }))
    expect(screen.getByText('(correct answer)').closest('li')).toHaveTextContent('c')
  })

  it('ignores an out-of-range numeric correct_answer', async () => {
    const user = userEvent.setup()
    renderWith([answerRow({
      questions: {
        question_text: 'Pick one', options: ['a', 'b'],
        correct_answer: '7', subject: 'algebra', difficulty: 'easy',
      },
    })])
    await user.click(await screen.findByRole('button', { name: /algebra/ }))
    expect(screen.queryByText('(correct answer)')).not.toBeInTheDocument()
  })

  it('prefers a value match over a numeric one', async () => {
    // An option literally named "1" must win over reading "1" as an index.
    const user = userEvent.setup()
    renderWith([answerRow({
      questions: {
        question_text: 'Pick one', options: ['0', '1', '2'],
        correct_answer: '1', subject: 'algebra', difficulty: 'easy',
      },
    })])
    await user.click(await screen.findByRole('button', { name: /algebra/ }))
    expect(screen.getByText('(correct answer)').closest('li')).toHaveTextContent('1')
  })
})

describe('engagement is not drawn beside focus', () => {
  it('gives the timeline table no Engagement column', async () => {
    // One number (signal_mapping.py); only the table shows whether the column is gone.
    apiFetch.mockResolvedValue({
      cognitive: [
        { ts: '2026-08-10T09:00:00Z', focus: 0.6, engagement: 0.6, stress: 0.4 },
        { ts: '2026-08-10T09:01:00Z', focus: 0.7, engagement: 0.7, stress: 0.4 },
      ],
      face: [], heart: [], answers: [],
    })
    renderAt()
    await waitFor(() => expect(screen.getByRole('columnheader', { name: 'Focus' })).toBeInTheDocument())
    expect(screen.queryByRole('columnheader', { name: /engagement/i })).not.toBeInTheDocument()
  })
})

describe('choosing which measurements the timeline draws', () => {
  // All four series live here; a hidden line takes its column with it.
  const WITH_HEART = {
    cognitive: [
      { ts: '2026-08-10T09:00:00Z', focus: 0.6, engagement: 0.6, stress: 0.4 },
      { ts: '2026-08-10T09:01:00Z', focus: 0.7, engagement: 0.7, stress: 0.3 },
    ],
    heart: [
      { ts: '2026-08-10T09:00:00Z', heart_rate_bpm: 72, rmssd_ms: 41, source: 'muse_optics' },
      { ts: '2026-08-10T09:01:00Z', heart_rate_bpm: 75, rmssd_ms: 38, source: 'muse_optics' },
    ],
    face: [],
    answers: [{
      answered_at: '2026-08-10T09:00:30Z', question_id: 'q-1',
      selected_index: 0, correct: true, questions: null,
    }],
  }

  it('hides RMSSD on its own, leaving heart rate drawn', async () => {
    apiFetch.mockResolvedValue(WITH_HEART)
    renderAt()
    await waitFor(() => expect(screen.getByRole('columnheader', { name: /rmssd/i })).toBeInTheDocument())

    await userEvent.click(screen.getByRole('switch', { name: /rmssd/i }))

    expect(screen.queryByRole('columnheader', { name: /rmssd/i })).not.toBeInTheDocument()
    expect(screen.getByRole('columnheader', { name: /heart rate/i })).toBeInTheDocument()
    expect(screen.getByRole('columnheader', { name: 'Focus' })).toBeInTheDocument()
  })

  it('offers no toggle for a measurement this session never recorded', async () => {
    apiFetch.mockResolvedValue({ ...WITH_HEART, heart: [] })
    renderAt()
    await waitFor(() => expect(screen.getByRole('columnheader', { name: 'Focus' })).toBeInTheDocument())

    expect(screen.queryByRole('switch', { name: /rmssd/i })).not.toBeInTheDocument()
    expect(screen.queryByRole('switch', { name: /heart rate/i })).not.toBeInTheDocument()
  })

  it('explains an empty timeline and offers a way back', async () => {
    apiFetch.mockResolvedValue(WITH_HEART)
    renderAt()
    await waitFor(() => expect(screen.getByRole('columnheader', { name: 'Focus' })).toBeInTheDocument())

    for (const name of [/^focus$/i, /eeg stress/i, /heart rate/i, /rmssd/i]) {
      await userEvent.click(screen.getByRole('switch', { name }))
    }

    expect(screen.getByText(/no measurements selected/i)).toBeInTheDocument()
    expect(screen.queryByRole('table', { name: /session replay/i })).not.toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: /show all/i }))
    expect(screen.getByRole('columnheader', { name: /rmssd/i })).toBeInTheDocument()
  })

  it('takes the answer-marker legend away with the markers themselves', async () => {
    // Markers use the ratio axis, so they go with Focus and EEG stress even while heart rate is drawn.
    apiFetch.mockResolvedValue(WITH_HEART)
    renderAt()
    await waitFor(() => expect(screen.getByText(/vertical lines = answer events/i)).toBeInTheDocument())

    await userEvent.click(screen.getByRole('switch', { name: /^focus$/i }))
    await userEvent.click(screen.getByRole('switch', { name: /eeg stress/i }))

    // Heart rate keeps the chart up, so this is not the empty-selection path.
    expect(screen.getByRole('columnheader', { name: /heart rate/i })).toBeInTheDocument()
    expect(screen.queryByText(/vertical lines = answer events/i)).not.toBeInTheDocument()

    // Not checked: the `ReferenceLine` gate itself, which jsdom cannot see.
  })
})
