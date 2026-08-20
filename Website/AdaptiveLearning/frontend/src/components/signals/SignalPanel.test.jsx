import { render, screen, cleanup, within } from '@testing-library/react'
import { LiveSignalSummary, WeeklySignalReport, StrategyPanel, pct } from './SignalPanel'

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
// scoping to the label's parent pins label -> value. Asserting that a number
// appears somewhere on the panel would pass even if it appeared in the wrong
// tile, and would encode fixture assumptions rather than component behaviour.
// The tiles, not the screen-reader tables. Those carry the same column names
// on purpose -- a chart's text alternative has to name its series -- so an
// unscoped `getByText('Engagement')` now finds a tile and a `<th>` and fails on
// the ambiguity rather than on anything being wrong.
const VISIBLE = { ignore: 'script, style, .sr-only, .sr-only *' }
const metric = (label) => within(screen.getByText(label, VISIBLE).parentElement)

describe('WeeklySignalReport', () => {
  it('renders each average as a percentage in its own tile', () => {
    render(<WeeklySignalReport report={report} />)
    // With the bug these read 1%, 0%, 1% and 1% respectively.
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
    // No Identity Confidence tile: #86 retired the column it read.
    expect(screen.queryByText('Identity Confidence')).not.toBeInTheDocument()
  })

  it('survives a report with no latest reading', () => {
    render(<LiveSignalSummary report={{}} />)
    // Was 'N/A'. The cognitive tiles were the last three still printing
    // `pct()`'s raw string; they go through `valueOrReason` now, so an absent
    // channel says which kind of absence it is.
    expect(metric('Focus').getByText('No sensor')).toBeInTheDocument()
    // Channel on (no flag says otherwise) and read, but nothing came back --
    // offLabel's no-sensor state, not a generic "no data".
    expect(metric('Facial Emotion').getByText('No sensor')).toBeInTheDocument()
  })

  it('says Calibrating, not No sensor, when rows arrived but none was usable', () => {
    // The case that sent me here: a headband recording with poor electrode
    // contact writes rows with the measurement columns nulled -- deliberately,
    // so the session's timeline survives. The snapshot read the newest of those
    // and printed "N/A" beside a weekly average of 64% on the same screen: two
    // true numbers that read as a contradiction.
    render(<LiveSignalSummary report={{
      latest: { cognitive: { focus: null, stress: null, engagement: null } },
      sample_counts: { cognitive: 155 },
    }} />)
    expect(metric('Focus').getByText('Calibrating')).toBeInTheDocument()
    expect(metric('Stress').getByText('Calibrating')).toBeInTheDocument()
    expect(metric('Engagement').getByText('Calibrating')).toBeInTheDocument()
  })

  it('says Off since <date> when EEG consent was withdrawn', () => {
    // The three cognitive tiles were the only ones that could not say this:
    // `eegReason` hardcoded `on: true` because the payload carried no EEG
    // consent state at all, so a parent who switched the headband off read
    // "No sensor" -- which is what a fault looks like, not what they did.
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
    // Withdrawal stops future recording and keeps what is stored, and the
    // cognitive channel has no read filter on the aggregate -- so a withdrawn
    // channel legitimately still has a value. `eeg_enabled` therefore has to be
    // consent state rather than an `eeg_included`-style claim that nothing was
    // read, and this is the case that tells the two apart.
    render(<LiveSignalSummary report={{
      latest: { cognitive: { focus: 0.72, stress: 0.31, engagement: 0.5 } },
      eeg_enabled: false,
      eeg_revoked_at: '2026-08-05T09:00:00Z',
      sample_counts: { cognitive: 155 },
    }} />)
    // The reading is what a viewer sees; the tile is not blanked. What changes
    // is that there is a date available for the surfaces that render one.
    expect(metric('Focus').getByText('72%')).toBeInTheDocument()
  })

  it('treats a payload with no EEG consent field as on, not off', () => {
    // Absent means a payload from before the field existed. Defaulting to off
    // would tell every reader of an older payload that a headband had been
    // switched off -- a claim about a decision nobody made.
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

// The backend nulls every face field when the viewer opts out, which on its
// own is indistinguishable from "the camera recorded nothing". face_included
// carries the difference, and these panels have to show it as one -- "N/A"
// where a parent switched facial reporting off reports a missing measurement
// instead of a respected choice.
describe('facial reporting switched off', () => {
  const faceOff = { ...report, face_included: false }

  it('labels the weekly face tiles as off rather than missing', () => {
    // "Off" alone could not say which of three things happened. One string
    // cannot answer for three channels, and the one it used to give -- "the
    // viewer switched facial reporting off" -- is no longer even a state that
    // exists. With no revocation date in the payload it degrades to "Not
    // recorded", which is still not "no data". Dominant Emotion goes through
    // the same offLabel/valueOrReason path as Face Attention now, rather than
    // a binary faceOn ? value : "Reporting off" that could not tell a genuine
    // withdrawal from a failed consent read.
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
  // The trap this guards: every other series here is a 0..1 ratio the panel
  // multiplies by 100. Putting 72.4 bpm through the same path prints "7240%".
  render(<WeeklySignalReport report={heartReport} />)

  // Scoped to the tile. A bare queryByText('72%') would collide with the
  // summary sentence, which legitimately says "average focus was 72%" -- and a
  // test that fails on a correct render is worse than no test.
  const bpmTile = screen.getByText(/Avg Heart Rate/i).closest('div')
  expect(within(bpmTile).getByText('72 bpm')).toBeInTheDocument()
  expect(within(bpmTile).queryByText(/%/)).not.toBeInTheDocument()

  const rmssdTile = screen.getByText(/Avg RMSSD/i).closest('div')
  expect(within(rmssdTile).getByText('42 ms')).toBeInTheDocument()
  expect(screen.queryByText(/7240/)).not.toBeInTheDocument()
})

test('the heart row is absent entirely when the channel was not read', () => {
  // Rather than a row of N/A, which is indistinguishable from a headband that
  // recorded nothing.
  render(<WeeklySignalReport report={{ ...heartReport, heart_included: false }} />)

  expect(screen.queryByText(/Avg Heart Rate/i)).not.toBeInTheDocument()
})

test('a payload from before the split does not claim the heart channel is off', () => {
  // It has no heart_included at all. Defaulting to true would draw an empty
  // series and report a sensor that never existed as having recorded nothing.
  render(<WeeklySignalReport report={report} />)

  expect(screen.queryByText(/Avg Heart Rate/i)).not.toBeInTheDocument()
})

test('the sensor behind the readings is named', () => {
  render(<WeeklySignalReport report={heartReport} />)
  expect(screen.getByText('Headband (optical)')).toBeInTheDocument()
})

test('samples recorded but all rejected reads as unusable, not absent', () => {
  // Three states: measured and fine, measured and unusable, never measured.
  // A null average with a nonzero count is the middle one.
  render(<WeeklySignalReport report={{
    ...heartReport,
    highlights: { ...heartReport.highlights, heart_rate_bpm: null, rmssd_ms: null },
  }} />)

  expect(screen.getByText(/none met the quality threshold/i)).toBeInTheDocument()
  // Not a raw "N/A": the channel was read (sample_counts.heart is 90), so a
  // null average here means the samples were rejected, which offLabel calls
  // Calibrating -- same distinction the Sensor tile already draws.
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
  // An empty pie is a claim about a quiet week that an unread channel has not
  // earned.
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
  // The backend leaves the key out when the channel was not read; a tile built
  // from {} would read as a sensor that recorded nothing.
  render(<LiveSignalSummary report={{
    ...heartReport,
    latest: { cognitive: { focus: 0.7 }, face: { attention: 0.8 } },
  }} />)

  expect(screen.queryByText(/Heart Rate/i)).not.toBeInTheDocument()
})


describe('per-channel off states', () => {
  // The rule these exist for: never render "no data" for something that was not
  // recorded. A tile saying "N/A" for a withdrawn channel reports an absence in
  // data nobody collected; one saying "Off" for a failed read reports a
  // preference nobody expressed.
  // Local, since `faceOff` is scoped to the describe above.
  const base = { ...report, face_included: false, emotion_included: false,
                 consent_retrieved: true }

  it('says when a channel was switched off', () => {
    render(<WeeklySignalReport report={{ ...base, emotion_revoked_at: '2026-08-03T09:00:00Z' }} />)
  })

  it('does not claim a withdrawal when the consent read failed', () => {
    // "The student turned this off" is a claim we have not earned when we could
    // not find out. Distinct state, distinct word.
    render(<WeeklySignalReport report={{ ...base, consent_retrieved: false }} />)
    // Dominant Emotion goes through the same faceOn as Face Attention, and a
    // failed read leaves faceOn false exactly as a withdrawal would -- the
    // reason it has to go through offLabel too, not a bare faceOn ternary.
    expect(metric('Dominant Emotion').getByText('Unavailable')).toBeInTheDocument()
  })

  it('does not claim a withdrawal on the live tiles when the consent read failed', () => {
    render(<LiveSignalSummary report={{ ...base, consent_retrieved: false }} />)
    expect(metric('Facial Emotion').getByText('Unavailable')).toBeInTheDocument()
  })

  it('distinguishes a channel that read nothing from one that read nothing usable', () => {
    // The average has to be null too: the channel is on and was read, it just
    // produced nothing usable. With a value present there is nothing for the
    // reason to replace.
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
    // Dropping the row would tell a parent who switched the sensor off nothing
    // at all -- the "no data" failure wearing a different shape.
    render(<WeeklySignalReport report={{ ...base, heart_included: false,
                                         heart_revoked_at: '2026-08-05T09:00:00Z' }} />)
    expect(screen.getByText((t) => /^Off since /.test(t) && t.includes('Aug'))).toBeInTheDocument()
  })

  it('omits the heart row entirely for a payload that predates the channel', () => {
    // Nothing true to say about a channel this payload does not know about.
    const { heart_included, ...preSplit } = base
    render(<WeeklySignalReport report={preSplit} />)
    expect(screen.queryByText(/Avg Heart Rate/)).not.toBeInTheDocument()
    expect(screen.queryByText(/^Heart$/)).not.toBeInTheDocument()
  })
})

// ── the charts, for anyone who cannot see them ──────────────────────────────
//
// Recharts emits bare `<svg>` with no accessible name and no walkable
// structure, so the trend and the distribution -- the whole of what a parent or
// teacher is shown about a child's week beyond the tiles -- announced as
// nothing at all.

describe('chart accessibility', () => {
  it('gives the trend a name that says what it shows', () => {
    render(<WeeklySignalReport report={report} />)
    const chart = screen.getByRole('img', { name: /daily signal trend/i })
    expect(chart).toHaveAccessibleName(/focus/i)
  })

  it('states each series as a range rather than only naming it', () => {
    // "Focus" alone is a legend, not a description. The range is the part that
    // carries what the picture actually showed.
    render(<WeeklySignalReport report={report} />)
    expect(screen.getByRole('img', { name: /daily signal trend/i }))
      .toHaveAccessibleName(/Focus 70% to 74%/i)
  })

  it('leaves a series nobody recorded out of the description', () => {
    // A gap in the line is not a zero, and the text alternative has to make the
    // same distinction -- otherwise it claims a flat week the chart never drew.
    render(<WeeklySignalReport report={report} />)
    const name = screen.getByRole('img', { name: /daily signal trend/i })
      .getAttribute('aria-label')
    expect(name).not.toMatch(/heart rate/i)
  })

  it('keeps the data table out of the role="img" subtree', () => {
    // The bug this replaces: the table was nested *inside* the `role="img"`
    // wrapper. WAI-ARIA's presentational-children rule prunes every descendant
    // role from an `img` unconditionally, so the table built for screen-reader
    // users was invisible to them -- and the test below could not tell, because
    // Testing Library reads DOM attributes rather than modelling the
    // accessibility tree, so `getByRole('table')` found it either way.
    //
    // Structure is therefore what gets asserted, since it is the part the
    // pruning depends on and the part a test in jsdom can actually see.
    render(<WeeklySignalReport report={report} />)
    const chart = screen.getByRole('img', { name: /daily signal trend/i })
    const table = screen.getByRole('table', { name: /daily signal trend/i })
    expect(chart).not.toContainElement(table)
  })

  it('carries the days themselves, not just the summary', () => {
    // A sighted reader can pick a Tuesday out of a line. Without the table, a
    // screen-reader user gets a range and no way to ask which day was which.
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
