import { render, screen } from '@testing-library/react'
import { LiveSignalSummary, WeeklySignalReport } from './SignalPanel'

// Signals cross the wire as 0..1 ratios -- that is what cognitive_signals and
// face_signals store. Rendering them unscaled printed focus 0.72 as "1%", so
// every metric on this panel came out ~100x too small (PR #22). These tests
// pin the scaling in both directions: the right number present, and the
// unscaled one absent.

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

describe('WeeklySignalReport', () => {
  it('renders 0..1 ratios as percentages', () => {
    render(<WeeklySignalReport report={report} />)
    expect(screen.getByText('72%')).toBeInTheDocument()
    expect(screen.getByText('31%')).toBeInTheDocument()
    expect(screen.getByText('85%')).toBeInTheDocument()
  })

  it('does not render the unscaled ratio', () => {
    // The original bug: Math.round(0.72) -> "1%". Asserting the correct value
    // alone would not catch it, because "1%" and "72%" can both be present in
    // a panel with several metrics.
    render(<WeeklySignalReport report={report} />)
    expect(screen.queryByText('1%')).not.toBeInTheDocument()
    expect(screen.queryByText('0%')).not.toBeInTheDocument()
  })

  it('shows N/A rather than a number when a metric is missing', () => {
    const partial = { ...report, averages: { ...report.averages, focus: null } }
    render(<WeeklySignalReport report={partial} />)
    expect(screen.getAllByText('N/A').length).toBeGreaterThan(0)
  })

  it('renders the session count raw, not as a percentage', () => {
    // Sessions is a count, so it deliberately bypasses the percent formatter.
    render(<WeeklySignalReport report={report} />)
    expect(screen.getByText('5')).toBeInTheDocument()
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
    expect(screen.getByText('72%')).toBeInTheDocument()
    expect(screen.getByText('93%')).toBeInTheDocument() // identity confidence
    expect(screen.queryByText('1%')).not.toBeInTheDocument()
  })

  it('survives a report with no latest reading', () => {
    render(<LiveSignalSummary report={{}} />)
    expect(screen.getAllByText('N/A').length).toBeGreaterThan(0)
    expect(screen.getByText('No data')).toBeInTheDocument()
  })
})
