import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { vi } from 'vitest'
import Students from './Students'
import { readHideSensorData, writeHideSensorData } from '../../lib/viewPrefs'
import { apiFetch, mockApi, overrideApi, resetApi, apiError } from '../../test/mocks/apiFetch'

// Roster from /api/teacher/students, totals from /api/stats/student/{id}, averages from
// /api/students/{id}/signal-summary, topics from /api/performance/student/{id}.
vi.mock('../../lib/api', async () => await import('../../test/mocks/apiFetch'))

// What /api/teacher/students returns: `user_id` and `name`, never `id` or `display_name`.
const ROSTER = [
  { user_id: 'stu-1', name: 'Ada Lovelace', email: 'ada@example.com',
    joined_at: '2026-01-15T09:00:00+00:00' },
  { user_id: 'stu-2', name: 'Grace Hopper', email: 'grace@example.com',
    joined_at: '2026-02-01T09:00:00+00:00' },
]
// A profile with no name, or no name and no email: both null, and sorted after every named row.
const NAMELESS = { user_id: 'stu-3', name: null, email: 'kid3@example.com',
                   joined_at: '2026-03-01T09:00:00+00:00' }
const NAMELESS_NO_EMAIL = { user_id: 'stu-4', name: null, email: null,
                            joined_at: '2026-03-02T09:00:00+00:00' }

// Seven days at 1 Hz: far above any row cap, so a count from rows cannot match.
const WEEK_OF_SAMPLES = 51840

const SUMMARY = {
  focus: 0.7, stress: 0.4, engagement: 0.6, face_attention: 0.9,
  sessions: 3, cognitive_samples: WEEK_OF_SAMPLES, face_samples: WEEK_OF_SAMPLES,
  face_included: true, dominant_emotion: 'happy',
}

// The endpoint's answer with facial off: facial fields empty, EEG and sessions untouched.
const SUMMARY_FACE_OFF = {
  ...SUMMARY, face_attention: null, face_samples: 0,
  face_included: false, dominant_emotion: null,
}

const USER_STATS = {
  data: { total_questions: 10, total_correct: 5, current_streak: 2, best_streak: 3 },
  error: null,
}

// An Error rejects; anything else resolves.
const answer = (value) => () => (value instanceof Error ? Promise.reject(value) : value)

function setData({ summary = SUMMARY, userStats = USER_STATS, roster = ROSTER, topics = [] } = {}) {
  // The endpoint returns the row itself; unwrap `{ data }` fixtures, pass Errors through.
  const stats = userStats instanceof Error ? userStats : (userStats?.data ?? userStats)
  mockApi([
    { match: '/api/teacher/students', handler: answer(roster) },
    { match: /^\/api\/stats\/student\//, handler: answer(stats) },
    { match: /\/signal-summary\?/, handler: answer(summary) },
    { match: /^\/api\/performance\/student\//, handler: answer(topics) },
  ])
}

const calls = () => apiFetch.mock.calls.map(([path]) => String(path))

// StatCard renders value, label and subtitle in one div.
function tile(label) {
  return within(screen.getByText(label).closest('div'))
}

async function expandAda() {
  await userEvent.click(await screen.findByRole('button', { name: /ada/i }))
}

const summaryCalls = () => calls().filter(p => p.includes('/signal-summary'))

beforeEach(() => {
  localStorage.clear()
  resetApi()
  setData()
})

it('reads the roster from the backend', async () => {
  // The frontend's Supabase client only signs in; it has no tables to read.
  render(<Students />)
  expect(await screen.findByText('Ada Lovelace')).toBeInTheDocument()
  expect(calls()).toContain('/api/teacher/students')
})

it('shows the class join date the roster carries', async () => {
  render(<Students />)
  const row = (await screen.findByText('Grace Hopper')).closest('button')
  const shown = new Date('2026-02-01T09:00:00+00:00')
    .toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' })
  expect(within(row).getByText(shown)).toBeInTheDocument()
})

it('asks for the expanded student, not the first one listed', async () => {
  render(<Students />)
  await userEvent.click(await screen.findByRole('button', { name: /grace/i }))
  await waitFor(() => expect(summaryCalls()).toHaveLength(1))
  expect(summaryCalls()[0]).toContain('/api/students/stu-2/signal-summary')
  expect(calls()).toContain('/api/performance/student/stu-2')
})

it('shows per-topic accuracy from the backend', async () => {
  // A `user_math_performance` row with its topic embedded, as the endpoint returns it.
  setData({ topics: [{ user_id: 'stu-1', topic_id: 3, attempted_questions: 4,
                       correct_questions: 3, updated_at: '2026-09-01T10:00:00+00:00',
                       math_topics: { topic_name: 'fractions' } }] })
  render(<Students />)
  await expandAda()
  expect(await screen.findByText('fractions')).toBeInTheDocument()
  expect(screen.getByText('3/4 correct')).toBeInTheDocument()
})

it('a failed topic read costs the topics panel only', async () => {
  setData({ topics: apiError(503) })
  render(<Students />)
  await expandAda()
  await waitFor(() => expect(tile('Focus Score').getByText('70%')).toBeInTheDocument())
  expect(tile('Total Accuracy').getByText('50%')).toBeInTheDocument()
  expect(screen.queryByText('Per-topic accuracy')).not.toBeInTheDocument()
})

describe('signal averages', () => {
  it('renders the aggregate as percentages', async () => {
    render(<Students />)
    await expandAda()
    await waitFor(() => expect(tile('Focus Score').getByText('70%')).toBeInTheDocument())
    expect(tile('Stress Level').getByText('40%')).toBeInTheDocument()
    // No Engagement tile: it is the focus index under another name.
    expect(screen.queryByText('Engagement')).not.toBeInTheDocument()
    expect(tile('Dominant Emotion').getByText('happy')).toBeInTheDocument()
  })

  it('reports the whole window, not a row cap', async () => {
    render(<Students />)
    await expandAda()
    await waitFor(() =>
      expect(tile('Focus Score').getByText(`${WEEK_OF_SAMPLES} EEG readings · last 7d`)).toBeInTheDocument())
  })

  it('asks the aggregate for the window the tiles claim', async () => {
    // Seven days, matching the weekly report, so teacher and parent describe the same week.
    render(<Students />)
    await expandAda()
    await waitFor(() => expect(summaryCalls()).toHaveLength(1))
    expect(summaryCalls()[0]).toContain('days=7')
    expect(summaryCalls()[0]).toContain('/api/students/stu-1/signal-summary')
  })

  it('reports no sensor rather than a confident zero when the aggregate has none', async () => {
    // The same reason every signal tile gives (`offLabel`): consented, nothing arrived.
    setData({ summary: { ...SUMMARY, focus: null, cognitive_samples: 0 } })
    render(<Students />)
    await expandAda()
    await waitFor(() => expect(tile('Focus Score').getByText('No sensor')).toBeInTheDocument())
  })

  it('does not render a missing field as NaN%', async () => {
    // Number(undefined) is NaN and Number(null) is 0.
    setData({ summary: {} })
    render(<Students />)
    await expandAda()
    await waitFor(() => expect(tile('Focus Score').getByText('No sensor')).toBeInTheDocument())
    expect(screen.queryByText(/NaN/)).not.toBeInTheDocument()
  })

  it('says a withdrawn EEG channel has been off since the day it was withdrawn', async () => {
    setData({ summary: { ...SUMMARY, focus: null, stress: null, cognitive_samples: 0,
                         eeg_enabled: false, eeg_revoked_at: '2026-09-03T09:00:00Z' } })
    render(<Students />)
    await expandAda()
    await waitFor(() => expect(tile('Focus Score').getByText(/^Off since/)).toBeInTheDocument())
  })
})

describe('a failed read', () => {
  it('costs the signal tiles only, not the academic ones beside them', async () => {
    setData({ summary: new Error('signal summary down') })
    render(<Students />)
    await expandAda()
    await waitFor(() => expect(tile('Total Accuracy').getByText('50%')).toBeInTheDocument())
    expect(tile('Focus Score').getByText('—')).toBeInTheDocument()
  })

  it('leaves the row refetchable rather than stuck loading', async () => {
    // A throw that leaves the loading flag set makes toggleExpand treat the row as handled.
    const statsCalls = () => calls().filter(p => p.includes('/api/stats/student/'))
    setData({ userStats: new Error('network down') })
    render(<Students />)
    await expandAda()
    await waitFor(() => expect(statsCalls()).toHaveLength(1))

    setData()
    await expandAda()   // collapse
    await expandAda()   // and retry
    await waitFor(() => expect(tile('Focus Score').getByText('70%')).toBeInTheDocument())
    expect(statsCalls().length).toBeGreaterThan(1)
  })

  it('costs the academic tiles only, not the signal ones beside them', async () => {
    setData({ userStats: new Error('stats down') })
    render(<Students />)
    await expandAda()

    await waitFor(() => expect(tile('Focus Score').getByText('70%')).toBeInTheDocument())
    expect(tile('Total Accuracy').getByText('—')).toBeInTheDocument()
  })

  it('says the academic figures could not be loaded rather than showing zero', async () => {
    setData({ userStats: new Error('stats down') })
    render(<Students />)
    await expandAda()

    await waitFor(() => expect(tile('Total Accuracy').getByText(/couldn't be loaded/i)).toBeInTheDocument())
    expect(tile('Total Accuracy').queryByText('0 questions')).not.toBeInTheDocument()
    // The streak has no natural "—" of its own.
    expect(tile('Current Streak').getByText('—')).toBeInTheDocument()
  })

  it('still reports a genuinely empty record as zero', async () => {
    // Teeth for the test above.
    setData({ userStats: { total_questions: 0, total_correct: 0, current_streak: 0,
                           best_streak: 0, retrieved: true } })
    render(<Students />)
    await expandAda()

    await waitFor(() => expect(tile('Total Accuracy').getByText('0 questions')).toBeInTheDocument())
    expect(tile('Total Accuracy').queryByText(/couldn't be loaded/i)).not.toBeInTheDocument()
  })
})

describe('facial recognition switch', () => {


  it('labels the facial tiles as off rather than missing', async () => {
    // Driven by face_included; "Hide sensor data" would hide these tiles entirely.
    setData({ summary: SUMMARY_FACE_OFF })
    render(<Students />)
    await expandAda()
    // `offLabel`'s wording for a channel off with no revocation date on the payload.
    expect(tile('Dominant Emotion').getByText('Not recorded')).toBeInTheDocument()
  })

  it('never calls a sensor off when the summary read failed', async () => {
    // A null summary read as heart "Off / not recorded": a claim about consent from a failed request.
    setData({ summary: new Error('down') })
    render(<Students />)
    await expandAda()
    for (const label of ['Avg Heart Rate', 'Avg HRV', 'Dominant Emotion']) {
      await waitFor(() => expect(tile(label).getByText('—')).toBeInTheDocument())
      expect(tile(label).getByText('signal data unavailable')).toBeInTheDocument()
    }
    expect(screen.queryByText('Off')).not.toBeInTheDocument()
  })

  it('does not call a sensor off when only the consent read failed', async () => {
    // Consent fails closed to heart off; a teacher must not read that as the family's decision.
    setData({ summary: { ...SUMMARY, heart_included: false, heart_samples: 0,
                         consent_retrieved: false } })
    render(<Students />)
    await expandAda()
    await waitFor(() => expect(tile('Avg Heart Rate').getByText('Unavailable')).toBeInTheDocument())
    expect(tile('Avg Heart Rate').getByText("consent couldn't be read")).toBeInTheDocument()
  })

  it('still says a withdrawn sensor is off when only the aggregate read failed', async () => {
    // Consent was read; only the figures failed, so the revocation is still known.
    setData({ summary: { ...SUMMARY, retrieved: false, consent_retrieved: true,
                         heart_included: false, heart_revoked_at: '2026-09-03T10:00:00Z',
                         heart_samples: 0, cognitive_samples: 0 } })
    render(<Students />)
    await expandAda()
    await waitFor(() => expect(tile('Avg Heart Rate').getByText(/^Off since/)).toBeInTheDocument())
    expect(tile('Avg Heart Rate').queryByText('signal data unavailable')).not.toBeInTheDocument()
    expect(tile('Focus Score').getByText('—')).toBeInTheDocument()
  })
})

describe('the "nothing recorded" note', () => {
  it('does not claim no sessions on the strength of facial data it never read', async () => {
    // face_samples is 0 by construction with facial off.
    setData({
      summary: { ...SUMMARY_FACE_OFF, cognitive_samples: 0 },
      userStats: { data: { total_questions: 0, total_correct: 0, current_streak: 0, best_streak: 0 }, error: null },
    })

    render(<Students />)
    await expandAda()

    expect(screen.queryByText(/hasn't completed any sessions yet/i)).not.toBeInTheDocument()
    expect(screen.getByText(/facial signals were not read/i)).toBeInTheDocument()
  })

  it('still says so when everything was read and there was nothing', async () => {
    setData({
      summary: { ...SUMMARY, cognitive_samples: 0, face_samples: 0 },
      userStats: { data: { total_questions: 0, total_correct: 0, current_streak: 0, best_streak: 0 }, error: null },
    })

    render(<Students />)
    await expandAda()

    await waitFor(() =>
      expect(screen.getByText(/hasn't completed any sessions yet/i)).toBeInTheDocument())
  })

  it('does not claim no sessions on the strength of a request that failed', async () => {
    // A caught summary failure leaves zero counts that must carry the failure with them.
    setData({
      summary: new Error('signal summary unavailable'),
      userStats: { data: { total_questions: 0, total_correct: 0, current_streak: 0, best_streak: 0 }, error: null },
    })

    render(<Students />)
    await expandAda()

    await waitFor(() => expect(screen.getByText(/couldn't be loaded/i)).toBeInTheDocument())
    expect(screen.queryByText(/hasn't completed any sessions yet/i)).not.toBeInTheDocument()
    expect(screen.queryByText(/facial signals were not read/i)).not.toBeInTheDocument()
  })

  it('does not claim no sessions when the endpoint answered 200 with defaults', async () => {
    // A swallowed aggregate answers 200 with defaults; only retrieved=false tells.
    setData({
      summary: { ...SUMMARY, focus: null, stress: null, engagement: null,
                 face_attention: null, sessions: 0, cognitive_samples: 0,
                 face_samples: 0, dominant_emotion: null, retrieved: false },
      userStats: { data: { total_questions: 0, total_correct: 0, current_streak: 0, best_streak: 0 }, error: null },
    })

    render(<Students />)
    await expandAda()

    await waitFor(() => expect(screen.getByText(/couldn't be loaded/i)).toBeInTheDocument())
    expect(screen.queryByText(/hasn't completed any sessions yet/i)).not.toBeInTheDocument()
    expect(tile('Focus Score').getByText(/signal data unavailable/i)).toBeInTheDocument()
  })

  it('treats a retrieved summary with nothing in it as a quiet week', async () => {
    setData({
      summary: { ...SUMMARY, cognitive_samples: 0, face_samples: 0, retrieved: true },
      userStats: { data: { total_questions: 0, total_correct: 0, current_streak: 0, best_streak: 0 }, error: null },
    })

    render(<Students />)
    await expandAda()

    await waitFor(() =>
      expect(screen.getByText(/hasn't completed any sessions yet/i)).toBeInTheDocument())
    expect(screen.queryByText(/couldn't be loaded/i)).not.toBeInTheDocument()
  })

  it('does not report a failed request as an absence of readings on the tiles', async () => {
    setData({ summary: new Error('signal summary unavailable') })

    render(<Students />)
    await expandAda()

    await waitFor(() => expect(tile('Focus Score').getByText(/signal data unavailable/i)).toBeInTheDocument())
    expect(tile('Focus Score').queryByText(/no EEG data/i)).not.toBeInTheDocument()
    // Read from user_stats, so the outage does not reach them.
    expect(tile('Total Accuracy').getByText('50%')).toBeInTheDocument()
  })

})


it('hides the sensor tiles without changing what it asks for', async () => {
  // Client-side display filter only; see lib/viewPrefs.js.
  expect(readHideSensorData()).toBe(false)
  writeHideSensorData(true)
  expect(readHideSensorData()).toBe(true)
})

it('actually hides the sensor tiles on screen when the switch is flipped, and leaves the rest', async () => {
  render(<Students />)
  await expandAda()

  for (const label of ['Stress Level', 'Focus Score',
                        'Dominant Emotion', 'Avg Heart Rate', 'Avg HRV',
                        'Total Accuracy', 'Current Streak']) {
    expect(screen.getByText(label)).toBeInTheDocument()
  }

  await userEvent.click(screen.getByRole('switch'))

  for (const label of ['Stress Level', 'Focus Score',
                        'Dominant Emotion', 'Avg Heart Rate', 'Avg HRV']) {
    expect(screen.queryByText(label)).not.toBeInTheDocument()
  }
  // Not sensor-derived, so unaffected.
  expect(screen.getByText('Total Accuracy')).toBeInTheDocument()
  expect(screen.getByText('Current Streak')).toBeInTheDocument()
  // Client-side only: no second request.
  expect(summaryCalls()).toHaveLength(1)
})

/** `profiles` has `display_name` and no `username`. */
it('names a student by their profile name, not by their email', async () => {
  render(<Students />)

  expect(await screen.findByText('Ada Lovelace')).toBeInTheDocument()
  expect(screen.queryByText('ada')).not.toBeInTheDocument()
})

it('searches the name on screen, not only the email behind it', async () => {
  render(<Students />)
  await screen.findByText('Ada Lovelace')

  await userEvent.type(screen.getByPlaceholderText(/search students/i), 'Lovelace')
  expect(screen.getByText('Ada Lovelace')).toBeInTheDocument()

  await userEvent.clear(screen.getByPlaceholderText(/search students/i))
  await userEvent.type(screen.getByPlaceholderText(/search students/i), 'nobody')
  expect(screen.queryByText('Ada Lovelace')).not.toBeInTheDocument()
})

describe('the roster read', () => {
  const rosterCalls = () => calls().filter(p => p === '/api/teacher/students')

  it('shows each student with their own email', async () => {
    // Two rows, so an email drawn on the wrong row fails.
    render(<Students />)
    for (const s of ROSTER) {
      const row = (await screen.findByText(s.name)).closest('button')
      expect(within(row).getByText(s.email)).toBeInTheDocument()
    }
  })

  it('labels a nameless student by their email and says no name is set', async () => {
    setData({ roster: [...ROSTER, NAMELESS] })
    render(<Students />)
    const row = (await screen.findByText(NAMELESS.email)).closest('button')
    expect(within(row).getByText('No name set')).toBeInTheDocument()
    // The badge is the row's only "Student": none is drawn as a name.
    expect(within(row).getAllByText('Student')).toHaveLength(1)
    const named = screen.getByText('Ada Lovelace').closest('button')
    expect(within(named).queryByText('No name set')).not.toBeInTheDocument()
  })

  it('says no name is set for a student with neither a name nor an email', async () => {
    setData({ roster: [...ROSTER, NAMELESS_NO_EMAIL] })
    render(<Students />)
    const row = (await screen.findByText('No name set')).closest('button')
    expect(within(row).getAllByText('Student')).toHaveLength(1)
    expect(within(row).getByText('?')).toBeInTheDocument()
  })

  it('answers a 503 with the load error and its retry, never a roster', async () => {
    // As apiFetch throws it once its Retry-After retries are spent.
    const failed = apiError(503, 'Could not load your students; try again')
    overrideApi('/api/teacher/students', answer(failed))
    render(<Students />)

    expect(await screen.findByText("Couldn't load your students just now. Try again in a moment."))
      .toBeInTheDocument()
    expect(screen.getByRole('button', { name: /try again/i })).toBeInTheDocument()
    expect(screen.queryByText('No students yet')).not.toBeInTheDocument()
    // Every row carries a "Student" badge.
    expect(screen.queryByText('Student')).not.toBeInTheDocument()
  })

  it('asks again on Try again and shows the roster that comes back', async () => {
    overrideApi('/api/teacher/students', answer(apiError(503, 'Could not load your students; try again')))
    render(<Students />)
    const retry = await screen.findByRole('button', { name: /try again/i })
    expect(rosterCalls()).toHaveLength(1)

    setData()
    await userEvent.click(retry)
    expect(await screen.findByText('Grace Hopper')).toBeInTheDocument()
    expect(rosterCalls()).toHaveLength(2)
    expect(screen.queryByRole('button', { name: /try again/i })).not.toBeInTheDocument()
  })

  it.each([
    ['a body that is not a list', { retrieved: false }],
    ['a row missing its name field', [{ user_id: 'stu-1', email: 'ada@example.com',
                                        joined_at: '2026-01-15T09:00:00+00:00' }]],
    ['a row missing its email field', [{ user_id: 'stu-1', name: 'Ada Lovelace',
                                         joined_at: '2026-01-15T09:00:00+00:00' }]],
    ['a row with no id', [{ name: 'Ada Lovelace', email: 'ada@example.com',
                            joined_at: '2026-01-15T09:00:00+00:00' }]],
  ])('treats %s as a failed read, not a roster', async (_label, body) => {
    setData({ roster: body })
    render(<Students />)

    expect(await screen.findByRole('button', { name: /try again/i })).toBeInTheDocument()
    expect(screen.queryByText('No students yet')).not.toBeInTheDocument()
    expect(screen.queryByText('Student')).not.toBeInTheDocument()
    expect(screen.queryByText('Ada Lovelace')).not.toBeInTheDocument()
    // The backend answered: "just now", never "make sure the backend is running".
    expect(screen.getByText("Couldn't load your students just now. Try again in a moment.")).toBeInTheDocument()
  })
})

it('still says No students yet for a roster that read as empty', async () => {
  setData({ roster: [] })
  render(<Students />)
  expect(await screen.findByText('No students yet')).toBeInTheDocument()
})
