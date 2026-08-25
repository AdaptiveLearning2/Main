import { render, screen, cleanup, within } from '@testing-library/react'
import { LiveSignalSummary, WeeklySignalReport, SignalTrend, StrategyPanel, pct } from './SignalPanel'

// Signals cross the wire as 0..1 ratios. Guards against rendering them
// unscaled, which would print focus 0.72 as "1%".

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

// Every metric renders its label and value as sibling <p>s in one wrapper, so
// scoping to the label's parent pins label -> value rather than asserting a
// number appears anywhere on the panel. Excludes the screen-reader tables,
// which share column names with the tiles on purpose.
const VISIBLE = { ignore: 'script, style, .sr-only, .sr-only *' }
const metric = (label) => within(screen.getByText(label, VISIBLE).parentElement)

describe('WeeklySignalReport', () => {
  it('renders each average as a percentage in its own tile', () => {
    render(<WeeklySignalReport report={report} />)
    // With the scaling bug these read 1%, 0%, 1%.
    expect(metric('Avg Focus').getByText('72%')).toBeInTheDocument()
    expect(metric('Avg Stress').getByText('31%')).toBeInTheDocument()
    expect(metric('Engagement').getByText('64%')).toBeInTheDocument()
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
    // sample_counts is rows-retrieved throughout, so under the session row
    // cap this tile showed the cap instead of the real count.
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
    // Neighbours unaffected.
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
    // Sessions have their own query and cap, so a day can lose only them --
    // counting the two signal flags alone made it look like a quiet day.
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
    // The backend swallows a failed table read so one broken query doesn't
    // blank the report, but that leaves N/A and a dash on the tiles, both of
    // which read as "nothing recorded" alone.
    const broken = {
      ...report,
      retrieved: { cognitive: false, face: true, sessions: true },
    }
    render(<WeeklySignalReport report={broken} />)
    expect(screen.getByText(/EEG signals.*could not be loaded/i)).toBeInTheDocument()
    expect(screen.queryByText(/session counts/i)).not.toBeInTheDocument()
  })

  it('shows a dash rather than zero when the sessions read failed', () => {
    // The fallback chain would otherwise answer a broken query with a
    // confident "0 sessions this week".
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
    // retrieved.face is null with the opt-out on -- no retrieval happened, so
    // the failure warning must not fire.
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
    // Payloads predating the field came from working reads, so an absent
    // `retrieved` must not raise the warning either.
    render(<WeeklySignalReport report={report} />)
    expect(screen.queryByText(/could not be loaded/i)).not.toBeInTheDocument()
  })
})

describe('LiveSignalSummary', () => {
  it('scales the latest reading to percentages', () => {
    render(<LiveSignalSummary report={report} />)
    expect(metric('Focus').getByText('72%')).toBeInTheDocument()
    expect(metric('Stress').getByText('31%')).toBeInTheDocument()
    // No Identity Confidence tile -- the column it read was retired.
    expect(screen.queryByText('Identity Confidence')).not.toBeInTheDocument()
  })

  it('survives a report with no latest reading', () => {
    render(<LiveSignalSummary report={{}} />)
    // Was 'N/A'; now goes through `valueOrReason` so an absent channel says
    // which kind of absence it is.
    expect(metric('Focus').getByText('No sensor')).toBeInTheDocument()
    // Channel on (no flag says otherwise) and read, but nothing came back.
    expect(metric('Facial Emotion').getByText('No sensor')).toBeInTheDocument()
  })

  it('says Calibrating, not No sensor, when rows arrived but none was usable', () => {
    // Poor electrode contact writes rows with the measurement columns
    // nulled, so the snapshot could print "N/A" beside a weekly average of
    // 64% on the same screen -- two true numbers that read as a contradiction.
    render(<LiveSignalSummary report={{
      latest: { cognitive: { focus: null, stress: null, engagement: null } },
      sample_counts: { cognitive: 155 },
    }} />)
    expect(metric('Focus').getByText('Calibrating')).toBeInTheDocument()
    expect(metric('Stress').getByText('Calibrating')).toBeInTheDocument()
    expect(metric('Engagement').getByText('Calibrating')).toBeInTheDocument()
  })

  it('says Off since <date> when EEG consent was withdrawn', () => {
    // Before `eeg_enabled` existed, `eegReason` hardcoded `on: true`, so a
    // parent who switched the headband off read "No sensor" -- a fault, not
    // what they did.
    render(<LiveSignalSummary report={{
      eeg_enabled: false,
      eeg_revoked_at: '2026-08-05T09:00:00Z',
      sample_counts: { cognitive: 0 },
    }} />)
    for (const tile of ['Focus', 'Stress', 'Engagement']) {
      expect(metric(tile).getByText((t) => /^Off since /.test(t) && t.includes('Aug')))
        .toBeInTheDocument()
    }
  })

  it('still reports a withdrawn EEG channel as off when it has readings behind it', () => {
    // Withdrawal stops future recording but keeps what's stored, and the
    // cognitive channel has no read filter -- so a withdrawn channel can
    // legitimately still have a value. `eeg_enabled` is consent state, not an
    // `eeg_included`-style claim that nothing was read.
    render(<LiveSignalSummary report={{
      latest: { cognitive: { focus: 0.72, stress: 0.31, engagement: 0.5 } },
      eeg_enabled: false,
      eeg_revoked_at: '2026-08-05T09:00:00Z',
      sample_counts: { cognitive: 155 },
    }} />)
    // The tile is not blanked -- what changes is a date becomes available for
    // surfaces that render one.
    expect(metric('Focus').getByText('72%')).toBeInTheDocument()
  })

  it('treats a payload with no EEG consent field as on, not off', () => {
    // Absent means a pre-field payload. Defaulting to off would claim a
    // headband was switched off when nobody made that decision.
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

// The backend nulls every face field when the viewer opts out, which alone
// is indistinguishable from "the camera recorded nothing". `face_included`
// carries the difference, and the panel must show it -- "N/A" for an opt-out
// would report a missing measurement instead of a respected choice.
describe('facial reporting switched off', () => {
  const faceOff = { ...report, face_included: false }

  it('labels the weekly face tiles as off rather than missing', () => {
    // Goes through the shared offLabel/valueOrReason path. With no
    // revocation date it degrades to "Not recorded", still not "no data".
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
    // Payloads predating the flag must keep rendering facial data.
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
    // Distinguishes the fixed rule set from model output that passed the
    // backend's safety checks.
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
    // The endpoint still answers with a usable list, but it's not about this
    // student's week, and the subtitle would otherwise claim it is.
    render(<StrategyPanel strategies={strategies} source="rule-based"
                          signalsRetrieved={false} onGenerate={() => {}} />)
    expect(screen.getByText(/general practice suggestions/i)).toBeInTheDocument()
    expect(screen.queryByText(/built from this week's report/i)).not.toBeInTheDocument()
    // Stated above the list too, since it changes how every item reads.
    expect(screen.getByText(/so these are general suggestions/i)).toBeInTheDocument()
    // Still shows the advice -- a generic list is correct here, not an error.
    expect(screen.getByText(strategies[0])).toBeInTheDocument()
  })

  it('keeps the default claim when the signals loaded', () => {
    render(<StrategyPanel strategies={strategies} source="rule-based"
                          signalsRetrieved={true} onGenerate={() => {}} />)
    expect(screen.getByText(/built from this week's report/i)).toBeInTheDocument()
    expect(screen.queryByText(/so these are general suggestions/i)).not.toBeInTheDocument()
  })

  it('treats a payload predating the field as a working read', () => {
    // Absent on older responses, from a working read -- undefined must not
    // read as a failure.
    render(<StrategyPanel strategies={strategies} source="rule-based" onGenerate={() => {}} />)
    expect(screen.getByText(/built from this week's report/i)).toBeInTheDocument()
    expect(screen.queryByText(/so these are general suggestions/i)).not.toBeInTheDocument()
  })
})

describe('pct', () => {
  // Shared with the parent dashboard, which used to have its own copy that a
  // fix to one didn't reach.
  it('scales a ratio to a whole percent', () => {
    expect(pct(0.72)).toBe('72%')
    expect(pct(0)).toBe('0%')       // a real zero reading still renders
  })

  it('reports an empty value as N/A rather than a confident zero', () => {
    // Number('') and Number('  ') are both 0, so these rendered as "0%".
    for (const v of ['', '   ', null, undefined]) expect(pct(v)).toBe('N/A')
  })

  it('reports a non-finite number as N/A', () => {
    // Number.isNaN(Infinity) is false, so this used to reach Math.round.
    for (const v of [Infinity, -Infinity, NaN, 'abc']) expect(pct(v)).toBe('N/A')
  })
})

// ── heart and emotion: two channels where there used to be one ──────────────

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
  // Every other series is a 0..1 ratio the panel multiplies by 100; putting
  // 72.4 bpm through the same path would print "7240%".
  render(<WeeklySignalReport report={heartReport} />)

  // Scoped to the tile -- a bare queryByText('72%') would collide with the
  // summary sentence, which legitimately says "average focus was 72%".
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
  // No heart_included at all -- defaulting to true would draw an empty
  // series claiming a sensor that never existed recorded nothing.
  render(<WeeklySignalReport report={report} />)

  expect(screen.queryByText(/Avg Heart Rate/i)).not.toBeInTheDocument()
})

test('the sensor behind the readings is named', () => {
  render(<WeeklySignalReport report={heartReport} />)
  expect(screen.getByText('Headband (optical)')).toBeInTheDocument()
})

test('samples recorded but all rejected reads as unusable, not absent', () => {
  // Three states: measured and fine, measured and unusable, never measured --
  // a null average with a nonzero count is the middle one.
  render(<WeeklySignalReport report={{
    ...heartReport,
    highlights: { ...heartReport.highlights, heart_rate_bpm: null, rmssd_ms: null },
  }} />)

  expect(screen.getByText(/none met the quality threshold/i)).toBeInTheDocument()
  // Not a raw "N/A": the channel was read, so a null average means the
  // samples were rejected, which offLabel calls Calibrating.
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
  // The backend leaves the key out when unread; a tile built from {} would
  // read as a sensor that recorded nothing.
  render(<LiveSignalSummary report={{
    ...heartReport,
    latest: { cognitive: { focus: 0.7 }, face: { attention: 0.8 } },
  }} />)

  expect(screen.queryByText(/Heart Rate/i)).not.toBeInTheDocument()
})


describe('per-channel off states', () => {
  // The rule: never render "no data" for something that was not recorded.
  // "N/A" for a withdrawn channel and "Off" for a failed read both make a
  // claim that isn't true. Local, since `faceOff` is scoped to the describe above.
  const base = { ...report, face_included: false, emotion_included: false,
                 consent_retrieved: true }

  it('says when a channel was switched off', () => {
    render(<WeeklySignalReport report={{ ...base, emotion_revoked_at: '2026-08-03T09:00:00Z' }} />)
  })

  it('does not claim a withdrawal when the consent read failed', () => {
    // "The student turned this off" is a claim we haven't earned when we
    // couldn't find out.
    render(<WeeklySignalReport report={{ ...base, consent_retrieved: false }} />)
    // A failed read leaves faceOn false exactly as a withdrawal would, so
    // this must go through offLabel, not a bare faceOn ternary.
    expect(metric('Dominant Emotion').getByText('Unavailable')).toBeInTheDocument()
  })

  it('does not claim a withdrawal on the live tiles when the consent read failed', () => {
    render(<LiveSignalSummary report={{ ...base, consent_retrieved: false }} />)
    expect(metric('Facial Emotion').getByText('Unavailable')).toBeInTheDocument()
  })

  it('distinguishes a channel that read nothing from one that read nothing usable', () => {
    // The average has to be null too -- with a value present there's nothing
    // for the reason to replace.
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
    // Nothing true to say about a channel this payload doesn't know about.
    const { heart_included, ...preSplit } = base
    render(<WeeklySignalReport report={preSplit} />)
    expect(screen.queryByText(/Avg Heart Rate/)).not.toBeInTheDocument()
    expect(screen.queryByText(/^Heart$/)).not.toBeInTheDocument()
  })
})

// ── the charts, for anyone who cannot see them ──────────────────────────────
//
// Recharts emits a bare `<svg>` with no accessible name, so the trend and the
// distribution charts announced as nothing at all.

describe('chart accessibility', () => {
  it('gives the trend a name that says what it shows', () => {
    render(<WeeklySignalReport report={report} />)
    const chart = screen.getByRole('img', { name: /daily signal trend/i })
    expect(chart).toHaveAccessibleName(/focus/i)
  })

  it('states each series as a range rather than only naming it', () => {
    // "Focus" alone is a legend, not a description -- the range carries what
    // the picture actually showed.
    render(<WeeklySignalReport report={report} />)
    expect(screen.getByRole('img', { name: /daily signal trend/i }))
      .toHaveAccessibleName(/Focus 70% to 74%/i)
  })

  it('leaves a series nobody recorded out of the description', () => {
    // A gap in the line is not a zero, and the text alternative has to make
    // the same distinction.
    render(<WeeklySignalReport report={report} />)
    const name = screen.getByRole('img', { name: /daily signal trend/i })
      .getAttribute('aria-label')
    expect(name).not.toMatch(/heart rate/i)
  })

  it('keeps the data table out of the role="img" subtree', () => {
    // The bug this replaces: the table nested *inside* the `role="img"`
    // wrapper. WAI-ARIA prunes every descendant role from an `img`, so the
    // table was invisible to assistive tech, but Testing Library reads DOM
    // attributes and found it either way -- so structure is what's asserted.
    render(<WeeklySignalReport report={report} />)
    const chart = screen.getByRole('img', { name: /daily signal trend/i })
    const table = screen.getByRole('table', { name: /daily signal trend/i })
    expect(chart).not.toContainElement(table)
  })

  it('carries the days themselves, not just the summary', () => {
    // Without the table, a screen-reader user gets a range and no way to ask
    // which day was which.
    render(<WeeklySignalReport report={report} />)
    const table = screen.getByRole('table', { name: /daily signal trend/i })
    expect(within(table).getByRole('rowheader', { name: '07-20' })).toBeInTheDocument()
    expect(within(table).getByRole('columnheader', { name: 'Focus' })).toBeInTheDocument()
  })

  it('says "not recorded" rather than leaving a cell blank', () => {
    // A blank cell is indistinguishable from a table that failed to render.
    //
    // Needs a column with a genuine gap, which now has to be arranged rather
    // than inherited from the fixture: the trend's columns are the series the
    // chart actually draws, and the base report's two are present on every day.
    // Heart is the one that legitimately comes and goes — a window can be
    // refused while the cognitive channel keeps recording.
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
  // The class of bug this whole component exists to prevent, and the one it
  // shipped with: an error that is *only* visible on the accessible surface,
  // where no sighted reviewer will ever meet it.

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
    // `chartData` scales `focus` and `stress` and spreads everything else
    // through unchanged, so a column added without a `scale` announces
    // "0% to 1%" while the visible lines beside it read 70% and 74%.
    render(<WeeklySignalReport report={RATIOS} />)
    // Both drawn series asserted positively. A negative like
    // `not.toMatch(/0% to 1%/)` looks like it covers this and does not: an
    // unscaled 0.64–0.68 rounds to "1%" at both ends and collapses to a single
    // "Engagement 1%", which that pattern never sees.
    expect(trendName()).toMatch(/Focus 70% to 74%/)
    expect(trendName()).toMatch(/Stress 30% to 32%/)
  })

  it('does not describe a series the chart does not draw', () => {
    // `engagement` has no `<Line>` on this chart. Describing it gives a
    // screen-reader user a series no sighted reader can see, which is a
    // different report rather than an equivalent one -- and it is exactly
    // where the scaling error hid, because nothing on screen contradicted it.
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
    // The failure this pins is invisible on screen and invisible to the
    // chart: the sr-only table is the only place the units are stated, so a
    // series scaled wrongly announces "0% to 1%" with nothing to contradict it.
    render(<SignalTrend trend={trend} />)

    const table = screen.getByRole('table')
    expect(within(table).getByText('62%')).toBeInTheDocument()
    expect(within(table).getByText('74 bpm')).toBeInTheDocument()
  })

  it('counts the weeks that recorded something, not the weeks in range', () => {
    // A week of three samples is plotted beside a week of four thousand and
    // looks equally solid. Coverage goes in the sentence rather than a column,
    // because a column naming something the chart does not draw would give a
    // screen-reader user a different report, not an equivalent one.
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
    // Both produce an empty series, and only the flag tells them apart.
    render(<SignalTrend trend={{ weeks: [], retrieved: false }} />)

    expect(screen.getByText(/could not be loaded/i)).toBeInTheDocument()
    expect(screen.queryByText(/no signal history yet/i)).not.toBeInTheDocument()
  })

  it('says a genuinely empty history is empty', () => {
    render(<SignalTrend trend={{ weeks: [], retrieved: true }} />)

    expect(screen.getByText(/no signal history yet/i)).toBeInTheDocument()
  })

  it('omits the heart series entirely when the channel is off', () => {
    // Not drawn as an all-null line: an empty legend entry reads as a
    // measurement that flatlined.
    render(<SignalTrend trend={{ ...trend, heart_included: false }} />)

    expect(screen.queryByText('Heart rate')).not.toBeInTheDocument()
    expect(screen.getByText('Focus')).toBeInTheDocument()
  })
})
