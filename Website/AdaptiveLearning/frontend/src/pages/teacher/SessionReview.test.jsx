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

// ─── the answers table ─────────────────────────────────────────────────────
//
// It showed a truncated uuid and a bare `selected_index` — both true and
// neither usable. A teacher scanning the column wants the topic, and "2" is
// not a fact anyone can act on without the options beside it.

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
    // Resolved by URL, not call order: the page fetches signals and charts in
    // parallel and a `mockResolvedValueOnce` chain would depend on whichever
    // Promise.all happened to start first.
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
    // PostgREST left-joins the embed, so this is a real shape. The answer
    // happened; dropping the row would change the session's history.
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
    // `questions.correct_answer` is text, not an index. Comparing positions
    // would mark the wrong option on every question whose answer is not
    // stored in order.
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
    // Not every generator dedupes its distractors, and two options both
    // ticked "correct answer" reads as a broken panel rather than a result.
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
    // Better to mark none than to mark an arbitrary one. A wrong marker on a
    // review screen is worse than an absent one.
    const user = userEvent.setup()
    renderWith([answerRow({
      questions: {
        question_text: 'What is 7 x 6?', options: ['40', '44'],
        correct_answer: '42', subject: 'algebra', difficulty: 'easy',
      },
    })])
    await user.click(await screen.findByRole('button', { name: /algebra/ }))
    expect(screen.queryByText('(correct answer)')).not.toBeInTheDocument()
    // The panel still shows the question and the pick.
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
    // The two are one number (signal_mapping.py). The sentence omits an
    // empty series on its own, so the table is the only surface that shows
    // whether the column is gone; nothing else would fail on its return.
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
  /**
   * This is the chart that carries all four — focus, EEG stress, heart rate
   * and RMSSD — so it is where a combination is worth anything. The rule under
   * test is the one this file already followed by hand: a column must name a
   * series the chart actually draws. Hiding a line makes that a thing a
   * teacher does, so the column has to follow, or the sr-only table keeps
   * saying "RMSSD: not recorded" on every row of a session that recorded it
   * fine and was simply not being shown.
   */
  const WITH_HEART = {
    cognitive: [
      { ts: '2026-08-10T09:00:00Z', focus: 0.6, engagement: 0.6, stress: 0.4 },
      { ts: '2026-08-10T09:01:00Z', focus: 0.7, engagement: 0.7, stress: 0.3 },
    ],
    heart: [
      { ts: '2026-08-10T09:00:00Z', heart_rate_bpm: 72, rmssd_ms: 41, source: 'muse_optics' },
      { ts: '2026-08-10T09:01:00Z', heart_rate_bpm: 75, rmssd_ms: 38, source: 'muse_optics' },
    ],
    face: [], answers: [],
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
    // A control whose only outcome is the one already on screen is worse than
    // its absence.
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
    // Not an empty chart with an empty table beside it.
    expect(screen.queryByRole('table', { name: /session replay/i })).not.toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: /show all/i }))
    expect(screen.getByRole('columnheader', { name: /rmssd/i })).toBeInTheDocument()
  })
})
