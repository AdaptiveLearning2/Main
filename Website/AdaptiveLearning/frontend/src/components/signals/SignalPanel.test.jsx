import { render, screen, within } from '@testing-library/react'
import { LiveSignalSummary, WeeklySignalReport } from './SignalPanel'

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

  it('renders without data', () => {
    render(<WeeklySignalReport report={null} />)
    expect(screen.getByText(/no weekly signal data available yet/i)).toBeInTheDocument()
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
