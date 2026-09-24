import { render, screen, cleanup, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { LiveSignalSummary, WeeklySignalReport, SignalTrend, StrategyPanel, pct } from './SignalPanel'

// Signals cross the wire as 0..1 ratios; unscaled, focus 0.72 prints "1%".

const report = {
  days: 7,
  averages: { focus: 0.72, stress: 0.31, engagement: 0.64, face_attention: 0.85 },
  highlights: { highest_stress: 0.91, lowest_focus: 0.12, dominant_emotion: 'neutral' },
  sample_counts: { cognitive: 120, face: 40, sessions: 5 },
  latest: {
    cognitive: { focus: 0.72, stress: 0.31, engagement: 0.64 },
    face: { attention: 0.85, emotion: 'happy' },
  },
  daily: [
    { date: '2026-07-20', focus: 0.7, stress: 0.3, attention: 0.8, cognitive_retrieved: true, face_retrieved: true },
    { date: '2026-07-21', focus: 0.74, stress: 0.32, attention: 0.9, cognitive_retrieved: true, face_retrieved: true },
  ],
  summary: 'This week, average focus was 72%.',
  truncated: false,
}

// Scopes to the label's wrapper to pin label -> value; excludes the sr-only tables, which share names.
const VISIBLE = { ignore: 'script, style, .sr-only, .sr-only *' }
const metric = (label) => within(screen.getByText(label, VISIBLE).parentElement)

describe('WeeklySignalReport', () => {
  it('renders each average as a percentage in its own tile', () => {
    render(<WeeklySignalReport report={report} />)
    // With the scaling bug these read 1%, 0%, 1%.
    expect(metric('Avg Focus').getByText('72%')).toBeInTheDocument()
    expect(metric('Avg Stress').getByText('31%')).toBeInTheDocument()
    // No Engagement tile: it is the focus index under another name.
    expect(screen.queryByText('Engagement', VISIBLE)).not.toBeInTheDocument()
  })

  it('renders each highlight as a percentage in its own tile', () => {
    render(<WeeklySignalReport report={report} />)
    expect(metric('Highest Stress').getByText('91%')).toBeInTheDocument()
    expect(metric('Lowest Focus').getByText('12%')).toBeInTheDocument()
    expect(metric('Dominant Emotion').getByText('neutral')).toBeInTheDocument()
  })

  it('renders the session count raw, not as a percentage', () => {
    render(<WeeklySignalReport report={report} />)
    expect(metric('Sessions').getByText('5')).toBeInTheDocument()
    expect(metric('Sessions').queryByText('500%')).not.toBeInTheDocument()
  })

  it('shows how many sessions there were, not how many rows came back', () => {
    // sample_counts is rows retrieved, which stops at the session row cap.
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
    // Sessions have their own query and cap, so a day can lose only them.
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
    // A swallowed read leaves N/A and a dash, which alone read as "nothing recorded".
    const broken = {
      ...report,
      retrieved: { cognitive: false, face: true, sessions: true },
    }
    render(<WeeklySignalReport report={broken} />)
    expect(screen.getByText(/EEG signals.*could not be loaded/i)).toBeInTheDocument()
    expect(screen.queryByText(/session counts/i)).not.toBeInTheDocument()
  })

  it('shows a dash rather than zero when the sessions read failed', () => {
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
    // retrieved.face is null under the opt-out: not requested, not failed.
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
    // An absent `retrieved` is a pre-field payload from a working read.
    render(<WeeklySignalReport report={report} />)
    expect(screen.queryByText(/could not be loaded/i)).not.toBeInTheDocument()
  })
})

describe('LiveSignalSummary', () => {
  it('scales the latest reading to percentages', () => {
    render(<LiveSignalSummary report={report} />)
    expect(metric('Focus').getByText('72%')).toBeInTheDocument()
    expect(metric('Stress').getByText('31%')).toBeInTheDocument()
    expect(screen.queryByText('Identity Confidence')).not.toBeInTheDocument()
  })

  it('survives a report with no latest reading', () => {
    render(<LiveSignalSummary report={{}} />)
    // Channel on (no flag says otherwise) and read, but nothing came back.
    expect(metric('Focus').getByText('No sensor')).toBeInTheDocument()
    expect(metric('Facial Emotion').getByText('No sensor')).toBeInTheDocument()
  })

  it('says Calibrating, not No sensor, when rows arrived but none was usable', () => {
    // Poor electrode contact writes rows with the measurement columns nulled.
    render(<LiveSignalSummary report={{
      latest: { cognitive: { focus: null, stress: null, engagement: null } },
      sample_counts: { cognitive: 155 },
    }} />)
    expect(metric('Focus').getByText('Calibrating')).toBeInTheDocument()
    expect(metric('Stress').getByText('Calibrating')).toBeInTheDocument()
    expect(screen.queryByText('Engagement', VISIBLE)).not.toBeInTheDocument()
  })

  it('says Off since <date> when EEG consent was withdrawn', () => {
    render(<LiveSignalSummary report={{
      eeg_enabled: false,
      eeg_revoked_at: '2026-08-05T09:00:00Z',
      sample_counts: { cognitive: 0 },
    }} />)
    for (const tile of ['Focus', 'Stress']) {
      expect(metric(tile).getByText((t) => /^Off since /.test(t) && t.includes('Aug')))
        .toBeInTheDocument()
    }
  })

  it('still reports a withdrawn EEG channel as off when it has readings behind it', () => {
    // Withdrawal keeps stored rows and EEG has no read filter, so a value can remain.
    render(<LiveSignalSummary report={{
      latest: { cognitive: { focus: 0.72, stress: 0.31, engagement: 0.5 } },
      eeg_enabled: false,
      eeg_revoked_at: '2026-08-05T09:00:00Z',
      sample_counts: { cognitive: 155 },
    }} />)
    expect(metric('Focus').getByText('72%')).toBeInTheDocument()
  })

  it('treats a payload with no EEG consent field as on, not off', () => {
    // Absent means a pre-field payload; off would claim a decision nobody made.
    render(<LiveSignalSummary report={{ sample_counts: { cognitive: 0 } }} />)
    expect(metric('Focus').getByText('No sensor')).toBeInTheDocument()
    expect(metric('Focus').queryByText(/^Off since /)).not.toBeInTheDocument()
  })

  it('does not claim a sensor state when the consent read failed', () => {
    render(<LiveSignalSummary report={{
      consent_retrieved: false,
      sample_counts: { cognitive: 0 },
    }} />)
    expect(metric('Focus').getByText('Unavailable')).toBeInTheDocument()
  })
})

// `face_included: false` separates an opt-out from a camera that recorded nothing.
describe('facial reporting switched off', () => {
  const faceOff = { ...report, face_included: false }

  it('labels the weekly face tiles as off rather than missing', () => {
    // With no revocation date offLabel says "Not recorded", still not "no data".
    render(<WeeklySignalReport report={faceOff} />)
    expect(metric('Dominant Emotion').getByText('Not recorded')).toBeInTheDocument()
    expect(screen.getByText(/facial recognition data was not included/i)).toBeInTheDocument()
  })

  it('labels the live face tiles as off rather than missing', () => {
    render(<LiveSignalSummary report={faceOff} />)
    expect(metric('Facial Emotion').getByText('Not recorded')).toBeInTheDocument()
  })

  it('leaves the EEG metrics untouched', () => {
    render(<WeeklySignalReport report={faceOff} />)
    expect(metric('Avg Focus').getByText('72%')).toBeInTheDocument()
    expect(metric('Avg Stress').getByText('31%')).toBeInTheDocument()
  })

  it('still reports face data when the flag is absent', () => {
    const { face_included, ...legacy } = faceOff
    render(<WeeklySignalReport report={legacy} />)
  })

  it('does not count the opt-out as data it failed to retrieve', () => {
    // face_retrieved is null (not requested), not false (cap stopped it).
    const truncated = {
      ...faceOff,
      truncated: true,
      daily: [{ date: '2026-07-20', focus: 0.7, stress: 0.3, attention: null, cognitive_retrieved: true, face_retrieved: null }],
    }
    render(<WeeklySignalReport report={truncated} />)
    expect(screen.queryByText(/could not be retrieved/i)).not.toBeInTheDocument()
  })
})


describe('StrategyPanel', () => {
  const strategies = ['Review fractions for ten minutes', 'Take a short break between sets']

  it('numbers the strategies and names their source', () => {
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
    render(<StrategyPanel strategies={strategies} source="rule-based"
                          signalsRetrieved={false} onGenerate={() => {}} />)
    expect(screen.getByText(/general practice suggestions/i)).toBeInTheDocument()
    expect(screen.queryByText(/built from this week's report/i)).not.toBeInTheDocument()
    expect(screen.getByText(/so these are general suggestions/i)).toBeInTheDocument()
    // A generic list is correct here, not an error.
    expect(screen.getByText(strategies[0])).toBeInTheDocument()
  })

  it('keeps the default claim when the signals loaded', () => {
    render(<StrategyPanel strategies={strategies} source="rule-based"
                          signalsRetrieved={true} onGenerate={() => {}} />)
    expect(screen.getByText(/built from this week's report/i)).toBeInTheDocument()
    expect(screen.queryByText(/so these are general suggestions/i)).not.toBeInTheDocument()
  })

  // The heading keeps "At-Home": the prompt and fallback are written for a parent.
  it('tells a teacher whose advice this is, without relabelling it as classroom advice', () => {
    render(<StrategyPanel strategies={strategies} source="rule-based"
                          signalsRetrieved={true} viewerRole="teacher" onGenerate={() => {}} />)
    expect(screen.getByRole('heading', { name: /at-home learning strategies/i })).toBeInTheDocument()
    expect(screen.getByText(/written for a family to use at home/i)).toBeInTheDocument()
    expect(screen.getByText(/share them rather than read them as classroom advice/i)).toBeInTheDocument()
  })

  it('says "this student" to a teacher where it says "your child" to a parent', () => {
    const { unmount } = render(
      <StrategyPanel strategies={strategies} source="rule-based"
                     signalsRetrieved={false} viewerRole="teacher" onGenerate={() => {}} />)
    expect(screen.getByText(/rather than ones based on this student.s report/i)).toBeInTheDocument()
    unmount()

    render(<StrategyPanel strategies={strategies} source="rule-based"
                          signalsRetrieved={false} viewerRole="parent" onGenerate={() => {}} />)
    expect(screen.getByText(/rather than ones based on your child.s report/i)).toBeInTheDocument()
  })

  it('reads an absent or unrecognised role as the parent it was written for', () => {
    render(<StrategyPanel strategies={strategies} source="rule-based"
                          signalsRetrieved={true} viewerRole="admin" onGenerate={() => {}} />)
    expect(screen.queryByText(/written for a family to use at home/i)).not.toBeInTheDocument()
    expect(screen.getByText(/built from this week's report/i)).toBeInTheDocument()
  })

  it('treats a payload predating the field as a working read', () => {
    render(<StrategyPanel strategies={strategies} source="rule-based" onGenerate={() => {}} />)
    expect(screen.getByText(/built from this week's report/i)).toBeInTheDocument()
    expect(screen.queryByText(/so these are general suggestions/i)).not.toBeInTheDocument()
  })
})

describe('pct', () => {
  it('scales a ratio to a whole percent', () => {
    expect(pct(0.72)).toBe('72%')
    expect(pct(0)).toBe('0%')       // a real zero reading still renders
  })

  it('reports an empty value as N/A rather than a confident zero', () => {
    // Number('') and Number('  ') are both 0.
    for (const v of ['', '   ', null, undefined]) expect(pct(v)).toBe('N/A')
  })

  it('reports a non-finite number as N/A', () => {
    // Number.isNaN(Infinity) is false.
    for (const v of [Infinity, -Infinity, NaN, 'abc']) expect(pct(v)).toBe('N/A')
  })
})

// ── heart and emotion ──────────────

const heartReport = {
  ...report,
  heart_included: true,
  emotion_included: true,
  consent_retrieved: true,
  highlights: { ...report.highlights, heart_rate_bpm: 72.4, rmssd_ms: 41.8 },
  sample_counts: { ...report.sample_counts, heart: 90 },
  heart_sources: ['muse_optics'],
  emotion_distribution: { happy: 12, neutral: 7, sad: 3 },
  retrieved: { cognitive: true, face: true, heart: true, sessions: true },
  daily: report.daily.map((d, i) => ({ ...d, heart_rate_bpm: 70 + i, heart_retrieved: true })),
}

test('heart figures render in absolute units, not as percentages', () => {
  // Through the ratio path 72.4 bpm would print "7240%".
  render(<WeeklySignalReport report={heartReport} />)

  // Scoped to the tile: the summary sentence legitimately says "72%".
  const bpmTile = screen.getByText(/Avg Heart Rate/i).closest('div')
  expect(within(bpmTile).getByText('72 bpm')).toBeInTheDocument()
  expect(within(bpmTile).queryByText(/%/)).not.toBeInTheDocument()

  const rmssdTile = screen.getByText(/Avg RMSSD/i).closest('div')
  expect(within(rmssdTile).getByText('42 ms')).toBeInTheDocument()
  expect(screen.queryByText(/7240/)).not.toBeInTheDocument()
})

test('the heart row is absent entirely when the channel was not read', () => {
  // Rather than a row of N/A, indistinguishable from a headband recording nothing.
  render(<WeeklySignalReport report={{ ...heartReport, heart_included: false }} />)

  expect(screen.queryByText(/Avg Heart Rate/i)).not.toBeInTheDocument()
})

test('a payload from before the split does not claim the heart channel is off', () => {
  // No heart_included at all: nothing true to say about the channel.
  render(<WeeklySignalReport report={report} />)

  expect(screen.queryByText(/Avg Heart Rate/i)).not.toBeInTheDocument()
})

test('the sensor behind the readings is named', () => {
  render(<WeeklySignalReport report={heartReport} />)
  expect(screen.getByText('Headband (optical)')).toBeInTheDocument()
})

test('samples recorded but all rejected reads as unusable, not absent', () => {
  // A null average with a nonzero count is "measured and unusable".
  render(<WeeklySignalReport report={{
    ...heartReport,
    highlights: { ...heartReport.highlights, heart_rate_bpm: null, rmssd_ms: null },
  }} />)

  expect(screen.getByText(/none met the quality threshold/i)).toBeInTheDocument()
  expect(metric('Avg Heart Rate').getByText('Calibrating')).toBeInTheDocument()
  expect(metric('Avg RMSSD').getByText('Calibrating')).toBeInTheDocument()
})

test('a failed consent read is not rendered as a refusal', () => {
  render(<WeeklySignalReport report={{
    ...heartReport, consent_retrieved: false, heart_included: false, emotion_included: false,
  }} />)

  expect(screen.getByText(/Consent settings could not be read/i)).toBeInTheDocument()
})

test('a failed heart read is named in the failure sentence', () => {
  render(<WeeklySignalReport report={{
    ...heartReport,
    retrieved: { cognitive: true, face: true, heart: false, sessions: true },
  }} />)

  expect(screen.getByText(/heart-rate signals.*could not be loaded/i)).toBeInTheDocument()
})

test('the emotion mix is rendered as a distribution, not just its argmax', () => {
  render(<WeeklySignalReport report={heartReport} />)
  expect(screen.getByText(/Emotion Mix/i, VISIBLE)).toBeInTheDocument()
})

test('no emotion mix is drawn when the channel was not read', () => {
  // An empty pie would claim a quiet week an unread channel hasn't earned.
  render(<WeeklySignalReport report={{
    ...heartReport, emotion_included: false, emotion_distribution: null,
  }} />)

  expect(screen.queryByText(/Emotion Mix/i, VISIBLE)).not.toBeInTheDocument()
})

test('the live snapshot shows heart in bpm when the channel was read', () => {
  render(<LiveSignalSummary report={{
    ...heartReport,
    latest: { cognitive: { focus: 0.7 }, face: { attention: 0.8 },
              heart: { heart_rate_bpm: 68.2, source: 'muse_optics' } },
  }} />)

  const tile = screen.getByText(/Heart Rate/i).closest('div')
  expect(within(tile).getByText('68 bpm')).toBeInTheDocument()
  expect(within(tile).queryByText(/%/)).not.toBeInTheDocument()
})

test('the live snapshot omits heart rather than showing an empty tile', () => {
  // The backend leaves the key out when unread.
  render(<LiveSignalSummary report={{
    ...heartReport,
    latest: { cognitive: { focus: 0.7 }, face: { attention: 0.8 } },
  }} />)

  expect(screen.queryByText(/Heart Rate/i)).not.toBeInTheDocument()
})


describe('per-channel off states', () => {
  // Never render "no data" for something that was not recorded.
  const base = { ...report, face_included: false, emotion_included: false,
                 consent_retrieved: true }

  it('says when a channel was switched off', () => {
    render(<WeeklySignalReport report={{ ...base, emotion_revoked_at: '2026-08-03T09:00:00Z' }} />)
  })

  it('does not claim a withdrawal when the consent read failed', () => {
    render(<WeeklySignalReport report={{ ...base, consent_retrieved: false }} />)
    // A failed read leaves faceOn false like a withdrawal, so this needs offLabel.
    expect(metric('Dominant Emotion').getByText('Unavailable')).toBeInTheDocument()
  })

  it('does not claim a withdrawal on the live tiles when the consent read failed', () => {
    render(<LiveSignalSummary report={{ ...base, consent_retrieved: false }} />)
    expect(metric('Facial Emotion').getByText('Unavailable')).toBeInTheDocument()
  })

  it('distinguishes a channel that read nothing from one that read nothing usable', () => {
    // The average must be null too, or there is nothing for the reason to replace.
    const on = { ...report, emotion_included: true, face_included: true,
                 consent_retrieved: true,
                 averages: { ...report.averages, face_attention: null } }

    // Readings arrived, none usable: calibrating, not absent.
    render(<WeeklySignalReport report={{ ...on, sample_counts: { face: 12 } }} />)
    cleanup()

    // Nothing arrived at all.
    render(<WeeklySignalReport report={{ ...on, sample_counts: { face: 0 } }} />)
  })

  it('keeps the heart row when the channel is off, with the reason in it', () => {
    // Dropping the row would tell a parent who switched the sensor off nothing.
    render(<WeeklySignalReport report={{ ...base, heart_included: false,
                                         heart_revoked_at: '2026-08-05T09:00:00Z' }} />)
    expect(screen.getByText((t) => /^Off since /.test(t) && t.includes('Aug'))).toBeInTheDocument()
  })

  it('omits the heart row entirely for a payload that predates the channel', () => {
    const { heart_included, ...preSplit } = base
    render(<WeeklySignalReport report={preSplit} />)
    expect(screen.queryByText(/Avg Heart Rate/)).not.toBeInTheDocument()
    expect(screen.queryByText(/^Heart$/)).not.toBeInTheDocument()
  })
})

// ── the charts, for anyone who cannot see them ──────────────────────────────

describe('chart accessibility', () => {
  it('gives the trend a name that says what it shows', () => {
    render(<WeeklySignalReport report={report} />)
    const chart = screen.getByRole('img', { name: /daily signal trend/i })
    expect(chart).toHaveAccessibleName(/focus/i)
  })

  it('states each series as a range rather than only naming it', () => {
    render(<WeeklySignalReport report={report} />)
    expect(screen.getByRole('img', { name: /daily signal trend/i }))
      .toHaveAccessibleName(/Focus 70% to 74%/i)
  })

  it('leaves a series nobody recorded out of the description', () => {
    render(<WeeklySignalReport report={report} />)
    const name = screen.getByRole('img', { name: /daily signal trend/i })
      .getAttribute('aria-label')
    expect(name).not.toMatch(/heart rate/i)
  })

  it('keeps the data table out of the role="img" subtree', () => {
    // ARIA prunes roles inside an `img` but jsdom does not, so assert structure.
    render(<WeeklySignalReport report={report} />)
    const chart = screen.getByRole('img', { name: /daily signal trend/i })
    const table = screen.getByRole('table', { name: /daily signal trend/i })
    expect(chart).not.toContainElement(table)
  })

  it('carries the days themselves, not just the summary', () => {
    render(<WeeklySignalReport report={report} />)
    const table = screen.getByRole('table', { name: /daily signal trend/i })
    expect(within(table).getByRole('rowheader', { name: '07-20' })).toBeInTheDocument()
    expect(within(table).getByRole('columnheader', { name: 'Focus' })).toBeInTheDocument()
  })

  it('says "not recorded" rather than leaving a cell blank', () => {
    // Needs a drawn series with a genuine gap; heart is the one that comes and goes.
    render(<WeeklySignalReport report={{
      ...report,
      heart_included: true,
      daily: [
        { ...report.daily[0], heart_rate_bpm: 68 },
        { ...report.daily[1], heart_rate_bpm: null },
      ],
    }} />)
    const table = screen.getByRole('table', { name: /daily signal trend/i })
    expect(within(table).getAllByText('not recorded').length).toBeGreaterThan(0)
  })

  it('keeps the emotion table out of its role="img" subtree too', () => {
    render(<WeeklySignalReport report={{
      ...report, emotion_included: true,
      emotion_distribution: { neutral: 40, happy: 12 },
    }} />)
    const chart = screen.getByRole('img', { name: /emotion mix/i })
    const table = screen.getByRole('table', { name: /emotion mix/i })
    expect(chart).not.toContainElement(table)
  })

  it('names the emotion mix by its slices', () => {
    render(<WeeklySignalReport report={{
      ...report, emotion_included: true,
      emotion_distribution: { neutral: 40, happy: 12 },
    }} />)
    expect(screen.getByRole('img', { name: /emotion mix/i }))
      .toHaveAccessibleName(/neutral 40 samples/i)
  })
})

describe('the trend description matches the trend', () => {
  // Errors here are visible only on the accessible surface.

  const RATIOS = {
    ...report,
    daily: [
      { date: '2026-07-20', focus: 0.7,  stress: 0.3, engagement: 0.64, cognitive_retrieved: true },
      { date: '2026-07-21', focus: 0.74, stress: 0.32, engagement: 0.68, cognitive_retrieved: true },
    ],
  }

  const trendName = () =>
    screen.getByRole('img', { name: /daily signal trend/i }).getAttribute('aria-label')

  it('reports percentages as percentages, not as the 0..1 they arrive as', () => {
    // `chartData` scales only focus and stress; a column without `scale` announces 0..1.
    render(<WeeklySignalReport report={RATIOS} />)
    // Positive matches: an unscaled range can collapse to "1%" and dodge a negative one.
    expect(trendName()).toMatch(/Focus 70% to 74%/)
    expect(trendName()).toMatch(/Stress 30% to 32%/)
  })

  it('does not describe a series the chart does not draw', () => {
    // `engagement` has no `<Line>` on this chart.
    render(<WeeklySignalReport report={RATIOS} />)
    expect(trendName()).not.toMatch(/engagement/i)
    expect(screen.getByRole('table', { name: /daily signal trend/i }))
      .not.toHaveTextContent(/Engagement/i)
  })

  it('leaves heart out of the description when the line is not drawn', () => {
    // The `<Line>` is conditional on consent, so the description has to be.
    render(<WeeklySignalReport report={{ ...RATIOS, heart_included: false }} />)
    expect(trendName()).not.toMatch(/heart rate/i)
  })
})

// ─── SignalTrend ─────────────────────────────────────────────────────────

const trend = {
  weeks: [
    { week_start: '2026-05-25', focus: 0.62, stress: 0.30, heart_rate_bpm: 74, days_with_data: 4 },
    { week_start: '2026-06-01', focus: null,  stress: null, heart_rate_bpm: null, days_with_data: 0 },
    { week_start: '2026-06-08', focus: 0.71, stress: 0.26, heart_rate_bpm: 71, days_with_data: 5 },
  ],
  retrieved: true,
  heart_included: true,
  emotion_included: true,
}

describe('SignalTrend', () => {
  afterEach(cleanup)

  it('scales the ratio series and leaves heart rate in bpm', () => {
    // The sr-only table is the only place the units are stated.
    render(<SignalTrend trend={trend} />)

    const table = screen.getByRole('table')
    expect(within(table).getByText('62%')).toBeInTheDocument()
    expect(within(table).getByText('74 bpm')).toBeInTheDocument()
  })

  it('counts the weeks that recorded something, not the weeks in range', () => {
    // Coverage goes in the sentence: a column must name a series the chart draws.
    render(<SignalTrend trend={trend} />)

    expect(screen.getByRole('img', { name: /across 3 weeks, with data recorded on 2 of them/i }))
      .toBeInTheDocument()
  })

  it('leaves a week with nothing recorded as a gap', () => {
    render(<SignalTrend trend={trend} />)

    const row = screen.getByRole('row', { name: /06-01/ })
    expect(within(row).getAllByText(/not recorded/i).length).toBeGreaterThan(0)
  })

  it('says a failed read failed, rather than that nothing was recorded', () => {
    render(<SignalTrend trend={{ weeks: [], retrieved: false }} />)

    expect(screen.getByText(/could not be loaded/i)).toBeInTheDocument()
    expect(screen.queryByText(/no signal history yet/i)).not.toBeInTheDocument()
  })

  it('says a genuinely empty history is empty', () => {
    render(<SignalTrend trend={{ weeks: [], retrieved: true }} />)

    expect(screen.getByText(/no signal history yet/i)).toBeInTheDocument()
  })

  it('omits the heart series entirely when the channel is off', () => {
    // Asserted on column and toggle: bare text matches both.
    render(<SignalTrend trend={{ ...trend, heart_included: false }} />)

    expect(screen.queryByRole('columnheader', { name: /heart rate/i })).not.toBeInTheDocument()
    expect(screen.queryByRole('switch', { name: /heart rate/i })).not.toBeInTheDocument()
    expect(screen.getByRole('columnheader', { name: /focus/i })).toBeInTheDocument()
  })
})

describe('the score-scale caption', () => {
  it('captions the term trend when its weeks straddle the change', () => {
    const weeks = [
      { week_start: '2026-05-25', focus: 0.62, stress: 0.30, days_with_data: 4, score_scale: { min: 1, max: 1 } },
      { week_start: '2026-06-01', focus: 0.71, stress: 0.26, days_with_data: 5, score_scale: { min: 2, max: 2 } },
    ]
    const { rerender } = render(<SignalTrend trend={{ weeks, retrieved: true }} />)
    expect(screen.getByRole('note')).toHaveTextContent(/not comparable/)
    rerender(<SignalTrend trend={{ weeks: weeks.map(w => ({ ...w, score_scale: { min: 2, max: 2 } })), retrieved: true }} />)
    expect(screen.queryByRole('note')).not.toBeInTheDocument()
  })

  it('captions the weekly averages when they collapse two scales into one number', () => {
    const { rerender } = render(<WeeklySignalReport report={{
      ...report, averages: { ...report.averages, score_scale: { min: 1, max: 2 } },
    }} />)
    expect(screen.getByRole('note')).toHaveTextContent(/not comparable/)
    rerender(<WeeklySignalReport report={report} />)
    expect(screen.queryByRole('note')).not.toBeInTheDocument()
  })
})

describe('choosing which measurements a chart draws', () => {
  afterEach(cleanup)

  // A hidden line takes its column with it. Assert on `columnheader`, never the summary sentence.

  it('drops both the line and its column when a measurement is switched off', async () => {
    render(<SignalTrend trend={trend} />)
    expect(screen.getByRole('columnheader', { name: /heart rate/i })).toBeInTheDocument()

    await userEvent.click(screen.getByRole('switch', { name: /heart rate/i }))

    expect(screen.queryByRole('columnheader', { name: /heart rate/i })).not.toBeInTheDocument()
    expect(screen.getByRole('columnheader', { name: /focus/i })).toBeInTheDocument()
    expect(screen.getByRole('columnheader', { name: /stress/i })).toBeInTheDocument()
  })

  it('says so on the toggle, not only by colour', async () => {
    render(<SignalTrend trend={trend} />)
    const heart = screen.getByRole('switch', { name: /heart rate/i })
    expect(heart).toHaveAttribute('aria-checked', 'true')

    await userEvent.click(heart)

    expect(screen.getByRole('switch', { name: /heart rate/i }))
      .toHaveAttribute('aria-checked', 'false')
  })

  it('takes any combination, not one at a time', async () => {
    render(<SignalTrend trend={trend} />)

    await userEvent.click(screen.getByRole('switch', { name: /focus/i }))
    await userEvent.click(screen.getByRole('switch', { name: /heart rate/i }))

    expect(screen.queryByRole('columnheader', { name: /focus/i })).not.toBeInTheDocument()
    expect(screen.queryByRole('columnheader', { name: /heart rate/i })).not.toBeInTheDocument()
    expect(screen.getByRole('columnheader', { name: /stress/i })).toBeInTheDocument()
  })

  it('puts a measurement back', async () => {
    render(<SignalTrend trend={trend} />)

    await userEvent.click(screen.getByRole('switch', { name: /stress/i }))
    expect(screen.queryByRole('columnheader', { name: /stress/i })).not.toBeInTheDocument()

    await userEvent.click(screen.getByRole('switch', { name: /stress/i }))
    expect(screen.getByRole('columnheader', { name: /stress/i })).toBeInTheDocument()
  })

  it('explains an empty chart and offers a way back, rather than refusing the last click', async () => {
    // A claim about the view, distinct from the claims about the data.
    render(<SignalTrend trend={trend} />)
    for (const name of [/focus/i, /stress/i, /heart rate/i]) {
      await userEvent.click(screen.getByRole('switch', { name }))
    }

    expect(screen.getByText(/no measurements selected/i)).toBeInTheDocument()
    expect(screen.queryByText(/no signal history yet/i)).not.toBeInTheDocument()
    // The chart is not rendered at all, so no empty table.
    expect(screen.queryByRole('table')).not.toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: /show all/i }))
    expect(screen.getByRole('columnheader', { name: /focus/i })).toBeInTheDocument()
  })

  it('draws a measurement that only becomes available later', () => {
    // The hook stores what is *hidden*, so a series that arrives later is shown.
    const { rerender } = render(<SignalTrend trend={{ ...trend, heart_included: false }} />)
    expect(screen.queryByRole('columnheader', { name: /heart rate/i })).not.toBeInTheDocument()

    rerender(<SignalTrend trend={trend} />)

    expect(screen.getByRole('columnheader', { name: /heart rate/i })).toBeInTheDocument()
    expect(screen.getByRole('switch', { name: /heart rate/i })).toHaveAttribute('aria-checked', 'true')
  })

  it('keeps a hidden measurement hidden across a re-render', async () => {
    const { rerender } = render(<SignalTrend trend={trend} />)
    await userEvent.click(screen.getByRole('switch', { name: /stress/i }))

    rerender(<SignalTrend trend={{ ...trend, retrieved: true }} />)

    expect(screen.queryByRole('columnheader', { name: /stress/i })).not.toBeInTheDocument()
  })

  it('paints each swatch with the colour its own line is stroked with', () => {
    // The swatch is aria-hidden and jsdom has no stylesheet, so only the inline style is checkable.
    render(<SignalTrend trend={trend} />)

    for (const name of [/^focus$/i, /stress/i, /heart rate/i]) {
      const swatch = screen.getByRole('switch', { name }).querySelector('[aria-hidden="true"]')
      expect(swatch.getAttribute('style')).toMatch(/#[0-9a-f]{6}|rgb\(/i)
      expect(swatch.getAttribute('style')).not.toMatch(/undefined/)
    }
    // The heart line's palette value, shared with SessionReview.
    expect(screen.getByRole('switch', { name: /heart rate/i })
      .querySelector('[aria-hidden="true"]')).toHaveStyle({ backgroundColor: '#a855f7' })
  })

  it('keeps the swatch colour when the measurement is switched off', async () => {
    // Hollow rather than gone: the chip still names its line.
    render(<SignalTrend trend={trend} />)
    await userEvent.click(screen.getByRole('switch', { name: /heart rate/i }))

    const swatch = screen.getByRole('switch', { name: /heart rate/i })
      .querySelector('[aria-hidden="true"]')
    expect(swatch.getAttribute('style')).toContain('a855f7')
    expect(swatch.getAttribute('style')).not.toMatch(/undefined/)
  })

  it('offers the same control on the daily chart', async () => {
    render(<WeeklySignalReport report={report} />)

    await userEvent.click(screen.getByRole('switch', { name: /^focus$/i }))

    expect(screen.queryByRole('columnheader', { name: /focus/i })).not.toBeInTheDocument()
    expect(screen.getByRole('columnheader', { name: /stress/i })).toBeInTheDocument()
  })
})
