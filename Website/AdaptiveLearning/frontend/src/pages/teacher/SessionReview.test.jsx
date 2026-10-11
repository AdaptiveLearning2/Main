import { render, screen, waitFor, within } from '@testing-library/react'
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
  it('names the heart pie Body arousal and draws the EEG score as calm, never "Stress"', async () => {
    apiFetch.mockResolvedValue({
      cognitive: [
        { ts: '2026-08-10T09:00:00Z', focus: 0.6, engagement: 0.5, stress: 0.25 },
        { ts: '2026-08-10T09:01:00Z', focus: 0.6, engagement: 0.5, stress: 0.25 },
      ],
      face: [],
      heart: [{ ts: '2026-08-10T09:00:30Z', stress_category: 'low' }],
      answers: [],
    })
    renderAt()

    await waitFor(() => expect(screen.getByRole('heading', { name: 'Body arousal (heart rate)' }))
      .toBeInTheDocument())
    expect(screen.queryByText(/stress/i)).not.toBeInTheDocument()
    // Plain words for the section, not "cognitive".
    expect(screen.getByRole('heading', { name: 'Readings through the lesson' })).toBeInTheDocument()
    expect(screen.queryByText(/cognitive/i)).not.toBeInTheDocument()
    // Stress 0.25 is calm 75%; no other series reads 75% or 25%.
    const replay = screen.getByRole('table', { name: /session replay: focus and calm/i })
    expect(within(replay).getByRole('columnheader', { name: 'Calm' })).toBeInTheDocument()
    expect(within(replay).getAllByText('75%').length).toBeGreaterThan(0)
    expect(within(replay).queryByText('25%')).not.toBeInTheDocument()
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
      expect(screen.getByAltText('Focus and EEG stress')).toBeInTheDocument())
    // A stored archive still draws stress, so the page says what its red line is.
    // Named by the legend's word, not by colour alone.
    expect(screen.getByText(/the line its legend calls “stress” is EEG stress, the inverse of calm/))
      .toBeInTheDocument()
    expect(screen.queryByText(/red line/)).not.toBeInTheDocument()
    expect(screen.getByAltText('Heart rate and heart-rate variability')).toBeInTheDocument()
    expect(screen.getByAltText('Emotion mix')).toBeInTheDocument()
    // Null means that channel drew nothing.
    expect(screen.queryByAltText('Body arousal (heart rate)')).not.toBeInTheDocument()
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
      expect(screen.getByAltText('Focus and EEG stress')).toBeInTheDocument())
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
      expect(screen.getByAltText('Focus and EEG stress')).toBeInTheDocument())
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

    await waitFor(() => expect(screen.getByRole('heading', { name: 'Body arousal (heart rate)' }))
      .toBeInTheDocument())
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

  it('draws one chart per unit, never percent beside bpm or ms', async () => {
    apiFetch.mockResolvedValue(WITH_HEART)
    renderAt()
    const pctTable = await screen.findByRole('table', { name: /session replay: focus and calm/i })
    const bpm = screen.getByRole('table', { name: /session replay: heart rate over/i })
    const ms = screen.getByRole('table', { name: /session replay: heart-rate variability/i })
    const headers = t => within(t).getAllByRole('columnheader').map(h => h.textContent).slice(1)
    expect(headers(pctTable)).toEqual(['Focus', 'Calm'])
    expect(headers(bpm)).toEqual(['Heart rate'])
    expect(headers(ms)).toEqual(['Heart-rate variability'])

    // Hiding a chart's only series unmounts that chart, not its neighbours.
    await userEvent.click(screen.getByRole('switch', { name: /heart-rate variability/i }))
    expect(screen.queryByRole('table', { name: /session replay: heart-rate variability/i })).not.toBeInTheDocument()
    expect(screen.getByRole('table', { name: /session replay: heart rate over/i })).toBeInTheDocument()
  })

  it('hides heart-rate variability on its own, leaving heart rate drawn', async () => {
    apiFetch.mockResolvedValue(WITH_HEART)
    renderAt()
    await waitFor(() => expect(screen.getByRole('columnheader', { name: /heart-rate variability/i })).toBeInTheDocument())

    await userEvent.click(screen.getByRole('switch', { name: /heart-rate variability/i }))

    expect(screen.queryByRole('columnheader', { name: /heart-rate variability/i })).not.toBeInTheDocument()
    expect(screen.getByRole('columnheader', { name: /heart rate/i })).toBeInTheDocument()
    expect(screen.getByRole('columnheader', { name: 'Focus' })).toBeInTheDocument()
  })

  it('offers no toggle for a measurement this session never recorded', async () => {
    apiFetch.mockResolvedValue({ ...WITH_HEART, heart: [] })
    renderAt()
    await waitFor(() => expect(screen.getByRole('columnheader', { name: 'Focus' })).toBeInTheDocument())

    expect(screen.queryByRole('switch', { name: /heart-rate variability/i })).not.toBeInTheDocument()
    expect(screen.queryByRole('switch', { name: /heart rate/i })).not.toBeInTheDocument()
  })

  it('explains an empty timeline and offers a way back', async () => {
    apiFetch.mockResolvedValue(WITH_HEART)
    renderAt()
    await waitFor(() => expect(screen.getByRole('columnheader', { name: 'Focus' })).toBeInTheDocument())

    for (const name of [/^focus$/i, /^calm$/i, /heart rate/i, /heart-rate variability/i]) {
      await userEvent.click(screen.getByRole('switch', { name }))
    }

    expect(screen.getByText(/no measurements selected/i)).toBeInTheDocument()
    expect(screen.queryByRole('table', { name: /session replay/i })).not.toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: /show all/i }))
    expect(screen.getByRole('columnheader', { name: /heart-rate variability/i })).toBeInTheDocument()
  })

  it('takes the answer-marker legend away with the markers themselves', async () => {
    // Markers use the ratio axis, so they go with Focus and Calm even while heart rate is drawn.
    apiFetch.mockResolvedValue(WITH_HEART)
    renderAt()
    await waitFor(() => expect(screen.getByText(/vertical lines = answer events/i)).toBeInTheDocument())

    await userEvent.click(screen.getByRole('switch', { name: /^focus$/i }))
    await userEvent.click(screen.getByRole('switch', { name: /^calm$/i }))

    // Heart rate keeps the chart up, so this is not the empty-selection path.
    expect(screen.getByRole('columnheader', { name: /heart rate/i })).toBeInTheDocument()
    expect(screen.queryByText(/vertical lines = answer events/i)).not.toBeInTheDocument()

    // Not checked: the `ReferenceLine` gate itself, which jsdom cannot see.
  })
})

// ── the emotion ribbon ──────────────────────────────────────────────────────

describe('the emotion ribbon', () => {
  it('draws the labels FER+ stores, and never a stand-in face for one it does not know', async () => {
    const labels = ['fear', 'angry', 'contempt', 'surprise', 'disgust', 'confused']
    apiFetch.mockResolvedValue({
      cognitive: [], heart: [], answers: [],
      face: labels.map((emotion, i) => ({
        ts: new Date(Date.UTC(2026, 7, 10, 9, 0, i * 20)).toISOString(), emotion })),
    })
    renderAt()

    for (const face of ['😨', '😠', '😒', '😮', '🤢']) {
      expect(await screen.findByText(face)).toBeInTheDocument()
    }
    // The pie's sr-only table names it too; this is the ribbon's cell.
    expect(within(screen.getByTitle(/— confused$/)).getByText('confused')).toBeInTheDocument()
    expect(screen.queryByText('🙂')).not.toBeInTheDocument()
  })
})

describe('a withdrawn channel', () => {
  const COG = [{ ts: '2026-08-10T09:00:00Z', focus: 0.6, engagement: 0.5, stress: 0.4 },
               { ts: '2026-08-10T09:01:00Z', focus: 0.6, engagement: 0.5, stress: 0.4 }]

  it('says the camera was turned off and when, not that there were no samples', async () => {
    apiFetch.mockResolvedValue({
      cognitive: COG, face: [], heart: [], answers: [],
      channels: { face_included: false, heart_included: true, consent_retrieved: true,
                  emotion_revoked_at: '2026-08-20T09:00:00Z' },
    })
    renderAt()
    expect(await screen.findByText(/The camera was turned off on .*so nothing it recorded is shown/)).toBeInTheDocument()
    expect(screen.queryByText('No face samples for this session.')).not.toBeInTheDocument()
  })

  it('never says a sensor that was never on was turned off, or that it recorded anything', async () => {
    apiFetch.mockResolvedValue({
      cognitive: COG, face: [], heart: [], answers: [],
      channels: { face_included: true, heart_included: false, consent_retrieved: true,
                  heart_revoked_at: null },
    })
    renderAt()
    expect(await screen.findByText('Heart-rate recording is off for this student, so nothing from it is shown.'))
      .toBeInTheDocument()
    expect(screen.queryByText(/turned off|recorded is/)).not.toBeInTheDocument()
  })

  it('says heart-rate recording was turned off under the timeline', async () => {
    apiFetch.mockResolvedValue({
      cognitive: COG, face: [], heart: [], answers: [],
      channels: { face_included: true, heart_included: false, consent_retrieved: true,
                  heart_revoked_at: '2026-08-19T09:00:00Z' },
    })
    renderAt()
    expect(await screen.findByText(/Heart-rate recording was turned off on/)).toBeInTheDocument()
  })

  it('says consent could not be checked rather than that it was withdrawn', async () => {
    apiFetch.mockResolvedValue({
      cognitive: COG, face: [], heart: [], answers: [],
      channels: { face_included: false, heart_included: false, consent_retrieved: false },
    })
    renderAt()
    expect(await screen.findByText(/Consent for the camera could not be checked/)).toBeInTheDocument()
    expect(screen.queryByText(/turned off/)).not.toBeInTheDocument()
  })

  it.each([[true, 'Off'], [false, 'Unavailable']])(
    'counts no face samples for a camera it never read (consent read: %s)', async (retrieved, shown) => {
      apiFetch.mockResolvedValue({
        cognitive: COG, face: [], heart: [], answers: [],
        channels: { face_included: false, heart_included: true, consent_retrieved: retrieved },
      })
      renderAt()
      const tile = (await screen.findByText('Camera readings')).parentElement.parentElement
      expect(within(tile).getByText(shown)).toBeInTheDocument()
      expect(within(tile).queryByText('0')).not.toBeInTheDocument()
    })

  it('names a chart withheld for unreadable consent as that, not as withdrawn', async () => {
    mockPair({ archived: true, charts: { cognitive_timeline: null }, unavailable: [], withdrawn: [],
               unchecked: ['emotion_pie', 'heart_rate', 'stress_pie'] })
    renderAt()
    expect(await screen.findByText('Not shown: consent for this sensor could not be checked.')).toBeInTheDocument()
    expect(screen.queryByText(/sensor is off/)).not.toBeInTheDocument()
  })

  it('names a withdrawn archived chart as withdrawn, not as empty', async () => {
    mockPair({ archived: true, charts: { cognitive_timeline: null }, unavailable: [],
               withdrawn: ['emotion_pie', 'heart_rate', 'stress_pie'] })
    renderAt()
    expect(await screen.findByText('Not shown: this sensor is off for this student.')).toBeInTheDocument()
    expect(screen.queryByText('Nothing was recorded on the camera channel.')).not.toBeInTheDocument()
  })
})
