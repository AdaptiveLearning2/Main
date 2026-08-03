import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { LiveSignalSummary, WeeklySignalReport, FacialRecognitionToggle, StrategyPanel, pct } from './SignalPanel'

// Signals cross the wire as 0..1 ratios -- that is what cognitive_signals and
// face_signals store. Rendering them unscaled printed focus 0.72 as "1%", so
// every metric on this panel came out ~100x too small (PR #22).

const report = {
  days: 7,
  averages: { focus: 0.72, stress: 0.31, engagement: 0.64, face_attention: 0.85 },
  highlights: { highest_stress: 0.91, lowest_focus: 0.12, dominant_emotion: 'neutral' },
  sample_counts: { cognitive: 120, face: 40, sessions: 5 },
  latest: {
    cognitive: { focus: 0.72, stress: 0.31, engagement: 0.64 },
    face: { attention: 0.85, emotion: 'happy', identity_confidence: 0.93 },
  },
  daily: [
    { date: '2026-07-20', focus: 0.7, stress: 0.3, attention: 0.8, cognitive_retrieved: true, face_retrieved: true },
    { date: '2026-07-21', focus: 0.74, stress: 0.32, attention: 0.9, cognitive_retrieved: true, face_retrieved: true },
  ],
  summary: 'This week, average focus was 72%.',
  truncated: false,
}

// Every metric renders its label and value as sibling <p>s in one wrapper, so
// scoping to the label's parent pins label -> value. Asserting that a number
// appears somewhere on the panel would pass even if it appeared in the wrong
// tile, and would encode fixture assumptions rather than component behaviour.
const metric = (label) => within(screen.getByText(label).parentElement)

describe('WeeklySignalReport', () => {
  it('renders each average as a percentage in its own tile', () => {
    render(<WeeklySignalReport report={report} />)
    // With the bug these read 1%, 0%, 1% and 1% respectively.
    expect(metric('Avg Focus').getByText('72%')).toBeInTheDocument()
    expect(metric('Avg Stress').getByText('31%')).toBeInTheDocument()
    expect(metric('Engagement').getByText('64%')).toBeInTheDocument()
    expect(metric('Face Attention').getByText('85%')).toBeInTheDocument()
  })

  it('renders each highlight as a percentage in its own tile', () => {
    render(<WeeklySignalReport report={report} />)
    expect(metric('Highest Stress').getByText('91%')).toBeInTheDocument()
    expect(metric('Lowest Focus').getByText('12%')).toBeInTheDocument()
    expect(metric('Dominant Emotion').getByText('neutral')).toBeInTheDocument()
  })

  it('renders the session count raw, not as a percentage', () => {
    // Sessions is a count, so it deliberately bypasses the percent formatter.
    render(<WeeklySignalReport report={report} />)
    expect(metric('Sessions').getByText('5')).toBeInTheDocument()
    expect(metric('Sessions').queryByText('500%')).not.toBeInTheDocument()
  })

  it('shows how many sessions there were, not how many rows came back', () => {
    // sample_counts is rows-retrieved throughout, so under the session row cap
    // this tile showed exactly the cap while the parent dashboard -- counting
    // the same week in Postgres -- showed the real number for the same child.
    const busy = {
      ...report,
      truncated: true,
      sample_counts: { ...report.sample_counts, sessions: 100 },
      sessions_recorded: 137,
    }
    render(<WeeklySignalReport report={busy} />)
    expect(metric('Sessions').getByText('137')).toBeInTheDocument()
    expect(metric('Sessions').queryByText('100')).not.toBeInTheDocument()
  })

  it('falls back to the row count for payloads predating the field', () => {
    render(<WeeklySignalReport report={report} />)
    expect(metric('Sessions').getByText('5')).toBeInTheDocument()
  })

  it('shows N/A in the affected tile when a metric is missing', () => {
    const partial = { ...report, averages: { ...report.averages, focus: null } }
    render(<WeeklySignalReport report={partial} />)
    expect(metric('Avg Focus').getByText('N/A')).toBeInTheDocument()
    // Its neighbours are unaffected.
    expect(metric('Avg Stress').getByText('31%')).toBeInTheDocument()
  })

  it('explains that gaps are unretrieved data, not absence of activity', () => {
    const truncated = {
      ...report,
      truncated: true,
      daily: [
        { date: '2026-07-15', focus: null, stress: null, attention: 0.8, cognitive_retrieved: false, face_retrieved: true },
        ...report.daily,
      ],
    }
    render(<WeeklySignalReport report={truncated} />)
    expect(screen.getByText(/could not be retrieved, not because there was no activity/i)).toBeInTheDocument()
  })

  it('counts a day whose sessions were cut as unretrieved', () => {
    // Sessions have their own query and their own cap, so a day can lose only
    // them. Counting the two signal flags alone left that day looking like a
    // day with nothing on it.
    const truncated = {
      ...report,
      truncated: true,
      daily: [
        { date: '2026-07-15', focus: 0.7, stress: 0.3, attention: 0.8,
          cognitive_retrieved: true, face_retrieved: true,
          sessions: null, sessions_retrieved: false },
        ...report.daily,
      ],
    }
    render(<WeeklySignalReport report={truncated} />)
    expect(screen.getByText(/could not be retrieved, not because there was no activity/i)).toBeInTheDocument()
  })

  it('renders without data', () => {
    render(<WeeklySignalReport report={null} />)
    expect(screen.getByText(/no weekly signal data available yet/i)).toBeInTheDocument()
  })

  it('names the reads that failed rather than showing their defaults as figures', () => {
    // The backend swallows a failed table read so one broken query does not
    // blank the report -- which leaves the tiles showing N/A and a dash, both
    // of which read as "nothing recorded" on their own.
    const broken = {
      ...report,
      retrieved: { cognitive: false, face: true, sessions: true },
    }
    render(<WeeklySignalReport report={broken} />)
    expect(screen.getByText(/EEG signals.*could not be loaded/i)).toBeInTheDocument()
    expect(screen.queryByText(/session counts/i)).not.toBeInTheDocument()
  })

  it('shows a dash rather than zero when the sessions read failed', () => {
    // sessions_recorded is null on that path and sample_counts.sessions is the
    // length of an empty list, so the fallback chain answered a broken query
    // with a confident "0 sessions this week".
    const broken = {
      ...report,
      sessions_recorded: null,
      sample_counts: { ...report.sample_counts, sessions: 0 },
      retrieved: { cognitive: true, face: true, sessions: false },
    }
    render(<WeeklySignalReport report={broken} />)
    expect(metric('Sessions').getByText('—')).toBeInTheDocument()
    expect(metric('Sessions').queryByText('0')).not.toBeInTheDocument()
  })

  it('does not read the facial opt-out as a failed facial read', () => {
    // retrieved.face is null with the opt-out on: there was no retrieval to
    // succeed or fail, and the warning must not fire on it.
    const off = {
      ...report,
      face_included: false,
      retrieved: { cognitive: true, face: null, sessions: true },
    }
    render(<WeeklySignalReport report={off} />)
    expect(screen.queryByText(/could not be loaded/i)).not.toBeInTheDocument()
    expect(screen.getByText(/facial recognition data was not included/i)).toBeInTheDocument()
  })

  it('does not claim an empty chart is a quiet week when the reads failed', () => {
    const broken = {
      ...report,
      daily: [],
      retrieved: { cognitive: false, face: false, sessions: false },
    }
    render(<WeeklySignalReport report={broken} />)
    expect(screen.getByText(/weekly signal data could not be loaded/i)).toBeInTheDocument()
    expect(screen.queryByText(/no weekly signal data available yet/i)).not.toBeInTheDocument()
  })

  it('stays quiet for a report whose reads all succeeded', () => {
    // Payloads predating the field came from working reads by definition, so
    // an absent `retrieved` must not raise the warning either.
    render(<WeeklySignalReport report={report} />)
    expect(screen.queryByText(/could not be loaded/i)).not.toBeInTheDocument()
  })
})

describe('LiveSignalSummary', () => {
  it('scales the latest reading to percentages', () => {
    render(<LiveSignalSummary report={report} />)
    expect(metric('Focus').getByText('72%')).toBeInTheDocument()
    expect(metric('Stress').getByText('31%')).toBeInTheDocument()
    expect(metric('Face Attention').getByText('85%')).toBeInTheDocument()
    expect(metric('Identity Confidence').getByText('93%')).toBeInTheDocument()
  })

  it('survives a report with no latest reading', () => {
    render(<LiveSignalSummary report={{}} />)
    expect(metric('Focus').getByText('N/A')).toBeInTheDocument()
    expect(metric('Facial Emotion').getByText('No data')).toBeInTheDocument()
  })
})

// The backend nulls every face field when the viewer opts out, which on its
// own is indistinguishable from "the camera recorded nothing". face_included
// carries the difference, and these panels have to show it as one -- "N/A"
// where a parent switched facial reporting off reports a missing measurement
// instead of a respected choice.
describe('facial reporting switched off', () => {
  const faceOff = { ...report, face_included: false }

  it('labels the weekly face tiles as off rather than missing', () => {
    render(<WeeklySignalReport report={faceOff} />)
    expect(metric('Face Attention').getByText('Off')).toBeInTheDocument()
    expect(metric('Dominant Emotion').getByText('Reporting off')).toBeInTheDocument()
    expect(screen.getByText(/facial recognition data was not included/i)).toBeInTheDocument()
  })

  it('labels the live face tiles as off rather than missing', () => {
    render(<LiveSignalSummary report={faceOff} />)
    expect(metric('Face Attention').getByText('Off')).toBeInTheDocument()
    expect(metric('Facial Emotion').getByText('Reporting off')).toBeInTheDocument()
    expect(metric('Identity Confidence').getByText('Reporting off')).toBeInTheDocument()
  })

  it('leaves the EEG metrics untouched', () => {
    render(<WeeklySignalReport report={faceOff} />)
    expect(metric('Avg Focus').getByText('72%')).toBeInTheDocument()
    expect(metric('Avg Stress').getByText('31%')).toBeInTheDocument()
  })

  it('still reports face data when the flag is absent', () => {
    // Payloads predating the flag must keep rendering facial data.
    const { face_included, ...legacy } = faceOff   // eslint-disable-line no-unused-vars
    render(<WeeklySignalReport report={legacy} />)
    expect(metric('Face Attention').getByText('85%')).toBeInTheDocument()
  })

  it('does not count the opt-out as data it failed to retrieve', () => {
    // face_retrieved is null (not requested), not false (the cap stopped us).
    const truncated = {
      ...faceOff,
      truncated: true,
      daily: [{ date: '2026-07-20', focus: 0.7, stress: 0.3, attention: null, cognitive_retrieved: true, face_retrieved: null }],
    }
    render(<WeeklySignalReport report={truncated} />)
    expect(screen.queryByText(/could not be retrieved/i)).not.toBeInTheDocument()
  })
})

describe('FacialRecognitionToggle', () => {
  it('exposes its state to assistive technology', () => {
    const { rerender } = render(<FacialRecognitionToggle enabled onChange={() => {}} />)
    expect(screen.getByRole('switch')).toHaveAttribute('aria-checked', 'true')
    rerender(<FacialRecognitionToggle enabled={false} onChange={() => {}} />)
    expect(screen.getByRole('switch')).toHaveAttribute('aria-checked', 'false')
  })

  it('reports the flipped value to its caller', async () => {
    const onChange = vi.fn()
    render(<FacialRecognitionToggle enabled onChange={onChange} />)
    await userEvent.click(screen.getByRole('switch'))
    expect(onChange).toHaveBeenCalledWith(false)
  })

  it('does not claim to control the camera', () => {
    // The switch decides what the report reads, and promising more than that
    // is a guarantee it cannot keep.
    render(<FacialRecognitionToggle enabled onChange={() => {}} />)
    expect(screen.getByText(/does not switch a camera on or off/i)).toBeInTheDocument()
  })
})

describe('StrategyPanel', () => {
  const strategies = ['Review fractions for ten minutes', 'Take a short break between sets']

  it('numbers the strategies and names their source', () => {
    // The source distinguishes the fixed rule set from model output that
    // passed the backend's safety checks -- worth seeing before acting on it.
    render(<StrategyPanel strategies={strategies} source="rule-based" onGenerate={() => {}} />)
    expect(screen.getByText(strategies[0])).toBeInTheDocument()
    expect(screen.getByText('Source: rule-based')).toBeInTheDocument()
  })

  it('shows an error instead of stale advice when generation fails', () => {
    render(<StrategyPanel strategies={strategies} error="Backend unavailable" onGenerate={() => {}} />)
    expect(screen.getByText('Backend unavailable')).toBeInTheDocument()
    expect(screen.queryByText(strategies[0])).not.toBeInTheDocument()
  })

  it('disables the button while generating', () => {
    render(<StrategyPanel loading onGenerate={() => {}} />)
    expect(screen.getByRole('button')).toBeDisabled()
  })

  it('invites generation before anything has been produced', () => {
    render(<StrategyPanel onGenerate={() => {}} />)
    expect(screen.getByText(/no strategies generated yet/i)).toBeInTheDocument()
  })

  it('retracts the "built from this week\'s report" claim when the signals did not load', () => {
    // The endpoint still answers with a usable list -- the rules read a null
    // average as no signal to act on and fall through to their generic
    // branches. But that list is not about this student's week, and the
    // subtitle says it is.
    render(<StrategyPanel strategies={strategies} source="rule-based"
                          signalsRetrieved={false} onGenerate={() => {}} />)
    expect(screen.getByText(/general practice suggestions/i)).toBeInTheDocument()
    expect(screen.queryByText(/built from this week's report/i)).not.toBeInTheDocument()
    // Stated above the list too, since it changes how every item reads.
    expect(screen.getByText(/so these are general suggestions/i)).toBeInTheDocument()
    // Still shows the advice: a generic list is the correct answer here, not
    // an error state.
    expect(screen.getByText(strategies[0])).toBeInTheDocument()
  })

  it('keeps the default claim when the signals loaded', () => {
    render(<StrategyPanel strategies={strategies} source="rule-based"
                          signalsRetrieved={true} onGenerate={() => {}} />)
    expect(screen.getByText(/built from this week's report/i)).toBeInTheDocument()
    expect(screen.queryByText(/so these are general suggestions/i)).not.toBeInTheDocument()
  })

  it('treats a payload predating the field as a working read', () => {
    // signals_retrieved is absent on older responses, which came from a
    // working read by definition -- undefined must not read as a failure.
    render(<StrategyPanel strategies={strategies} source="rule-based" onGenerate={() => {}} />)
    expect(screen.getByText(/built from this week's report/i)).toBeInTheDocument()
    expect(screen.queryByText(/so these are general suggestions/i)).not.toBeInTheDocument()
  })
})

describe('pct', () => {
  // Shared with the parent dashboard, which had a verbatim copy -- so a fix to
  // one of them did not reach the other. Both guarded with
  // Number.isNaN(Number(v)), which lets two kinds of non-measurement through.
  it('scales a ratio to a whole percent', () => {
    expect(pct(0.72)).toBe('72%')
    expect(pct(0)).toBe('0%')       // a real zero reading still renders
  })

  it('reports an empty value as N/A rather than a confident zero', () => {
    // Number('') and Number('  ') are both 0, so these rendered as "0%" --
    // a measurement of a struggling student, out of no measurement at all.
    for (const v of ['', '   ', null, undefined]) expect(pct(v)).toBe('N/A')
  })

  it('reports a non-finite number as N/A', () => {
    // Number.isNaN(Infinity) is false, so this reached Math.round.
    for (const v of [Infinity, -Infinity, NaN, 'abc']) expect(pct(v)).toBe('N/A')
  })
})
