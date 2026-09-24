import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { vi } from 'vitest'
import Students from './Students'
import { readHideSensorData, writeHideSensorData } from '../../lib/viewPrefs'

// Totals from /api/stats/student/{id}, averages from /api/students/{id}/signal-summary; topics via supabase.

vi.mock('../../lib/api', () => {
  const apiCalls = []
  // Next response: an object resolves, an Error rejects, a Promise is adopted as-is.
  const state = { summary: null, userStats: null }
  return {
    apiFetch: (path) => {
      apiCalls.push(path)
      const r = String(path).includes('/api/stats/student/') ? state.userStats : state.summary
      return r instanceof Error ? Promise.reject(r) : Promise.resolve(r)
    },
    __apiCalls: apiCalls,
    __apiState: state,
  }
})

vi.mock('../../lib/supabase', () => {
  const fromCalls = []
  const results = {}
  // Chainable builder; also a thenable, since the topic query is awaited straight off .eq().
  const query = (table) => {
    // A stored Error rejects, to exercise the throw path.
    const settle = () => {
      const r = results[table] ?? { data: [], error: null }
      return r instanceof Error ? Promise.reject(r) : Promise.resolve(r)
    }
    const q = {
      select: () => q,
      eq: () => q,
      order: () => q,
      limit: () => settle(),
      maybeSingle: () => settle(),
      then: (res, rej) => settle().then(res, rej),
    }
    return q
  }
  return {
    supabase: {
      auth: { getUser: () => Promise.resolve({ data: { user: { id: 'teacher-1' } }, error: null }) },
      from: (table) => { fromCalls.push(table); return query(table) },
    },
    __fromCalls: fromCalls,
    __results: results,
  }
})

const { __fromCalls: fromCalls, __results: results } = await import('../../lib/supabase')
const { __apiCalls: apiCalls, __apiState: apiState } = await import('../../lib/api')

// Real `profiles` columns only: there is no `username`.
const MEMBERSHIPS = {
  data: [{
    student_id: 'stu-1',
    profiles: { id: 'stu-1', email: 'ada@example.com', display_name: 'Ada Lovelace',
                role: 'student', grade_level: '6th Grade' },
    classes: { teacher_id: 'teacher-1' },
  }],
  error: null,
}

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

function setData({ summary = SUMMARY, userStats = USER_STATS } = {}) {
  for (const k of Object.keys(results)) delete results[k]
  Object.assign(results, {
    class_memberships: MEMBERSHIPS,
    user_math_performance: { data: [], error: null },
  })
  apiState.summary = summary
  // The endpoint returns the row itself; unwrap `{ data }` fixtures, pass Errors through.
  apiState.userStats = userStats instanceof Error ? userStats : (userStats?.data ?? userStats)
}

// StatCard renders value, label and subtitle in one div.
function tile(label) {
  return within(screen.getByText(label).closest('div'))
}

async function expandAda() {
  await userEvent.click(await screen.findByRole('button', { name: /ada/i }))
}

const summaryCalls = () => apiCalls.filter(p => p.includes('/signal-summary'))

beforeEach(() => {
  localStorage.clear()
  fromCalls.length = 0
  apiCalls.length = 0
  setData()
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

  it('reports no data rather than a confident zero when the aggregate has none', async () => {
    setData({ summary: { ...SUMMARY, focus: null, cognitive_samples: 0 } })
    render(<Students />)
    await expandAda()
    await waitFor(() => expect(tile('Focus Score').getByText('—')).toBeInTheDocument())
  })

  it('does not render a missing field as NaN%', async () => {
    // Number(undefined) is NaN and Number(null) is 0.
    setData({ summary: {} })
    render(<Students />)
    await expandAda()
    await waitFor(() => expect(tile('Focus Score').getByText('—')).toBeInTheDocument())
    expect(screen.queryByText(/NaN/)).not.toBeInTheDocument()
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
    const statsCalls = () => apiCalls.filter(p => String(p).includes('/api/stats/student/'))
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
    expect(tile('Dominant Emotion').getByText('Off')).toBeInTheDocument()
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

it('falls back to the email prefix for a student with no name set', async () => {
  // Unreachable from the app, but the column is nullable and the SQL editor writes it.
  results.class_memberships = {
    data: [{
      student_id: 'stu-1',
      profiles: { id: 'stu-1', email: 'ada@example.com', display_name: null,
                  role: 'student', grade_level: '6th Grade' },
      classes: { teacher_id: 'teacher-1' },
    }],
    error: null,
  }
  render(<Students />)

  expect(await screen.findByText('ada')).toBeInTheDocument()
})
