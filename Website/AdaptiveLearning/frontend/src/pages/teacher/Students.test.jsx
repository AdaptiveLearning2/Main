import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { vi } from 'vitest'
import Students from './Students'
import { readHideSensorData, writeHideSensorData } from '../../lib/viewPrefs'

// This page reads user_stats and topic performance from the browser client, and
// its four signal averages from /api/students/{id}/signal-summary.
//
// The averages used to come from a direct cognitive_signals / face_signals read
// capped at 200 rows. At the poller's 1 Hz default that cap binds after about
// three minutes, so tiles labelled "last 7d" were averaging the newest three
// minutes of one sitting and reporting a reading count pinned at exactly 200 as
// a count of the week -- while the parent dashboard, aggregating the same week
// in Postgres, showed different numbers for the same student. The tests below
// cover the endpoint that replaced it, and that the facial switch still stops
// the facial data being asked for rather than merely hiding it.

vi.mock('../../lib/api', () => {
  const apiCalls = []
  // The next signal-summary response. A plain object resolves, an Error
  // rejects, and a Promise is adopted as-is -- which is what the staleness
  // tests need in order to land two responses out of order.
  const state = { summary: null }
  return {
    apiFetch: (path) => {
      apiCalls.push(path)
      const r = state.summary
      return r instanceof Error ? Promise.reject(r) : Promise.resolve(r)
    },
    __apiCalls: apiCalls,
    __apiState: state,
  }
})

vi.mock('../../lib/supabase', () => {
  const fromCalls = []
  const results = {}
  // Every builder method the page chains returns the builder; the terminal
  // calls resolve. The builder is itself a thenable because the topic query is
  // awaited straight off .eq() with no limit()/maybeSingle().
  const query = (table) => {
    // An Error stored for a table rejects rather than resolving, so a test can
    // exercise the throw path as well as the { data, error } one.
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

const MEMBERSHIPS = {
  data: [{
    student_id: 'stu-1',
    profiles: { id: 'stu-1', email: 'ada@example.com', username: 'ada' },
    classes: { teacher_id: 'teacher-1' },
  }],
  error: null,
}

// Seven days at 1 Hz across a few sittings. Deliberately far above the 200-row
// cap the browser read used to impose, so a count rendered from rows rather
// than from the aggregate cannot accidentally match.
const WEEK_OF_SAMPLES = 51840

const SUMMARY = {
  focus: 0.7, stress: 0.4, engagement: 0.6, face_attention: 0.9,
  sessions: 3, cognitive_samples: WEEK_OF_SAMPLES, face_samples: WEEK_OF_SAMPLES,
  face_included: true, dominant_emotion: 'happy',
}

// What the endpoint returns for the same student with the switch off: no facial
// row is read, so every facial field comes back empty and face_included says
// why. Sessions and EEG are untouched by the switch.
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
    user_stats: userStats,
    user_math_performance: { data: [], error: null },
  })
  apiState.summary = summary
}

// StatCard renders the value, label and subtitle in one div, so scope by the
// label's parent.
function tile(label) {
  return within(screen.getByText(label).closest('div'))
}

async function expandAda() {
  await userEvent.click(await screen.findByRole('button', { name: /ada/i }))
}

const summaryCalls = () => apiCalls.filter(p => p.includes('/signal-summary'))

function deferred() {
  let resolve
  const promise = new Promise(r => { resolve = r })
  return { promise, resolve }
}

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
    expect(tile('Engagement').getByText('60%')).toBeInTheDocument()
    expect(tile('Face Attention').getByText('90%')).toBeInTheDocument()
    expect(tile('Dominant Emotion').getByText('happy')).toBeInTheDocument()
  })

  it('reports the whole window, not a row cap', async () => {
    // The regression this endpoint exists for. The browser read it replaced was
    // capped at 200 rows, so a busy week rendered "200 EEG readings · last 7d"
    // -- a cap presented as a measurement of the week, and a number the parent
    // dashboard contradicted for the same student.
    render(<Students />)
    await expandAda()
    await waitFor(() =>
      expect(tile('Focus Score').getByText(`${WEEK_OF_SAMPLES} EEG readings · last 7d`)).toBeInTheDocument())
    expect(tile('Face Attention').getByText(`${WEEK_OF_SAMPLES} face readings · last 7d`)).toBeInTheDocument()
  })

  it('asks the aggregate for the window the tiles claim', async () => {
    // The tiles say "last 7d", so the request has to say seven days -- matching
    // the weekly report and the summary RPCs' p_days default, so that a teacher
    // and a parent describe the same week.
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
    // "0%" here would read as a real measurement of a struggling student.
    await waitFor(() => expect(tile('Focus Score').getByText('—')).toBeInTheDocument())
  })

  it('does not render a missing field as NaN%', async () => {
    // Number(undefined) is NaN and Number(null) is 0, so converting without a
    // finite check turns an absent field into "NaN%" or a confident "0%".
    setData({ summary: {} })
    render(<Students />)
    await expandAda()
    await waitFor(() => expect(tile('Focus Score').getByText('—')).toBeInTheDocument())
    expect(screen.queryByText(/NaN/)).not.toBeInTheDocument()
  })
})

describe('a failed read', () => {
  it('costs the signal tiles only, not the academic ones beside them', async () => {
    // The summary is one of three parallel reads. Letting it reject the whole
    // Promise.all would blank stats that loaded perfectly well.
    setData({ summary: new Error('signal summary down') })
    render(<Students />)
    await expandAda()
    await waitFor(() => expect(tile('Total Accuracy').getByText('50%')).toBeInTheDocument())
    expect(tile('Focus Score').getByText('—')).toBeInTheDocument()
  })

  it('leaves the row refetchable rather than stuck loading', async () => {
    // The loading flag is what toggleExpand checks to decide a row is already
    // handled. A read that throws without clearing it leaves the row showing a
    // spinner that collapsing and re-expanding never clears -- the same stuck
    // row the supersede path is careful to avoid.
    setData({ userStats: new Error('network down') })
    render(<Students />)
    await expandAda()
    await waitFor(() => expect(fromCalls.filter(t => t === 'user_stats')).toHaveLength(1))

    setData()
    await expandAda()   // collapse
    await expandAda()   // and retry
    await waitFor(() => expect(tile('Focus Score').getByText('70%')).toBeInTheDocument())
    expect(fromCalls.filter(t => t === 'user_stats').length).toBeGreaterThan(1)
  })
})

describe('facial recognition switch', () => {


  it('labels the facial tiles as off rather than missing', async () => {
    // face_included, not the "Hide sensor data" switch, is what drives these
    // labels -- that switch now actually hides the sensor tiles (see below),
    // so clicking it here would hide the very tiles this test reads.
    setData({ summary: SUMMARY_FACE_OFF })
    render(<Students />)
    await expandAda()
    await waitFor(() => expect(tile('Face Attention').getByText('Off')).toBeInTheDocument())
    expect(tile('Dominant Emotion').getByText('Off')).toBeInTheDocument()
  })




})

describe('the "nothing recorded" note', () => {
  it('does not claim no sessions on the strength of facial data it never read', async () => {
    // face_samples is 0 by construction with the switch off, so including it in
    // the condition unconditionally let this assert "no sessions" for a student
    // whose only recorded activity was the facial signals we were asked not to
    // look at.
    setData({
      summary: { ...SUMMARY_FACE_OFF, cognitive_samples: 0 },
      userStats: { data: { total_questions: 0, total_correct: 0, current_streak: 0, best_streak: 0 }, error: null },
    })

    render(<Students />)
    await expandAda()

    await waitFor(() => expect(tile('Face Attention').getByText('Off')).toBeInTheDocument())
    expect(screen.queryByText(/hasn't completed any sessions yet/i)).not.toBeInTheDocument()
    // What was actually checked, stated as such.
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
    // The same mistake as the facial one above, reached from the other side.
    // The summary request is caught individually so an outage costs the signal
    // tiles rather than the academic ones beside them -- which leaves every
    // signal count at its zero default, indistinguishable from a student who
    // recorded nothing unless the failure travels with them.
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
    // The other half of the same finding. The backend swallows a failed
    // aggregate so one broken read does not blank the page, and answers 200
    // with an all-default summary -- so the request succeeds, and only
    // retrieved=false distinguishes it from a student who recorded nothing.
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
    // The mirror: retrieved=true with zero samples is a real answer about a
    // student who recorded nothing, and must still say so. Marking it as a
    // failure would collapse the distinction from the other side.
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
    // "no EEG data · last 7d" is the same claim in miniature: the count it
    // quotes is zero because nothing came back, not because nothing was
    // recorded. The academic tiles beside it are unaffected, which is the
    // point of catching the summary separately.
    setData({ summary: new Error('signal summary unavailable') })

    render(<Students />)
    await expandAda()

    await waitFor(() => expect(tile('Focus Score').getByText(/signal data unavailable/i)).toBeInTheDocument())
    expect(tile('Focus Score').queryByText(/no EEG data/i)).not.toBeInTheDocument()
    expect(tile('Face Attention').getByText(/signal data unavailable/i)).toBeInTheDocument()
    // Read from user_stats, so the outage does not reach them.
    expect(tile('Total Accuracy').getByText('50%')).toBeInTheDocument()
  })

})


it('hides the sensor tiles without changing what it asks for', async () => {
  // The teacher's remaining switch is a display filter. The old one narrowed
  // the query, so every cached row and in-flight read had to be superseded when
  // it flipped; this one does not, which is why it is client-side.
  //
  // Consent still decides what the server will return, and a channel that is
  // off for consent reasons still says so when the filter is off -- the filter
  // hides data, it never manufactures a reason. See lib/viewPrefs.js.
  expect(readHideSensorData()).toBe(false)
  writeHideSensorData(true)
  expect(readHideSensorData()).toBe(true)
})

it('actually hides the sensor tiles on screen when the switch is flipped, and leaves the rest', async () => {
  // The switch used to update state and localStorage and nothing else -- the
  // stat tiles below it were never gated on it, so flipping it visibly did
  // nothing. This is the regression test for that: it renders the tiles, not
  // just the preference.
  render(<Students />)
  await expandAda()

  for (const label of ['Stress Level', 'Focus Score', 'Engagement', 'Face Attention',
                        'Dominant Emotion', 'Avg Heart Rate', 'Avg HRV',
                        'Total Accuracy', 'Current Streak']) {
    expect(screen.getByText(label)).toBeInTheDocument()
  }

  await userEvent.click(screen.getByRole('switch'))

  for (const label of ['Stress Level', 'Focus Score', 'Engagement', 'Face Attention',
                        'Dominant Emotion', 'Avg Heart Rate', 'Avg HRV']) {
    expect(screen.queryByText(label)).not.toBeInTheDocument()
  }
  // Not sensor-derived, so unaffected by a display preference over sensor data.
  expect(screen.getByText('Total Accuracy')).toBeInTheDocument()
  expect(screen.getByText('Current Streak')).toBeInTheDocument()
  // Client-side only: hiding these tiles must not have asked the server again.
  expect(summaryCalls()).toHaveLength(1)
})
