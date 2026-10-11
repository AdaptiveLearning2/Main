import { render, screen, cleanup, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { LiveSignalSummary, WeeklySignalReport, SignalTrend, StrategyPanel, ChartSummaryPanel } from './SignalPanel'
import { pct } from '../../lib/signalFormat'
import { buildWeeklyReport, buildBodyArousal, buildUsualMeasure } from '../../test/fixtures/signalSummary'

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
const VISIBLE = { ignore: 'script, style, .sr-only, .sr-only *, [hidden], [hidden] *' }
// A tile is a labelled group; a highlight box is its label's parent. Either pins label -> value.
const metric = (label) => within(
  screen.queryByRole('group', { name: label }) ?? screen.getByText(label, VISIBLE).parentElement)

describe('WeeklySignalReport', () => {
  it('renders each average as a percentage in its own tile', () => {
    render(<WeeklySignalReport report={report} />)
    // With the scaling bug these read 1%, 0%, 1%. Calm is stress the right way up: 0.31 -> 69%.
    expect(metric('Focus').getByText('72%')).toBeInTheDocument()
    expect(metric('Calm').getByText('69%')).toBeInTheDocument()
    expect(screen.queryByText(/stress/i, VISIBLE)).not.toBeInTheDocument()
    // No Engagement tile: it is the focus index under another name.
    expect(screen.queryByText('Engagement', VISIBLE)).not.toBeInTheDocument()
  })

  it('renders each highlight as a percentage in its own tile', () => {
    render(<WeeklySignalReport report={report} />)
    // The highest stress reading is the lowest calm one: 0.91 -> 9%.
    expect(metric('Lowest calm reading').getByText('9%')).toBeInTheDocument()
    expect(metric('Lowest focus reading').getByText('12%')).toBeInTheDocument()
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

  it('says why a tile has no figure, in the affected tile only, never a raw N/A', () => {
    // Rows arrived (120) and none gave a focus: calibrating, not missing.
    const partial = { ...report, averages: { ...report.averages, focus: null } }
    render(<WeeklySignalReport report={partial} />)
    expect(metric('Focus').getByText('Calibrating')).toBeInTheDocument()
    expect(metric('Calm').getByText('69%')).toBeInTheDocument()
    expect(screen.queryByText('N/A', VISIBLE)).not.toBeInTheDocument()
  })

  it('says Off since <date> on all four EEG tiles when EEG was withdrawn', () => {
    const withdrawn = {
      ...report,
      eeg_enabled: false, eeg_revoked_at: '2026-09-03T09:00:00Z',
      averages: { ...report.averages, focus: null, stress: null },
      highlights: { ...report.highlights, highest_stress: null, lowest_focus: null },
      sample_counts: { ...report.sample_counts, cognitive: 0 },
    }
    render(<WeeklySignalReport report={withdrawn} />)
    for (const label of ['Focus', 'Calm', 'Lowest calm reading', 'Lowest focus reading']) {
      expect(metric(label).getByText(/^Off since/)).toBeInTheDocument()
    }
  })

  it('calls a failed EEG read unavailable, not No sensor', () => {
    // The failed read's counts are zero, which alone reads as a headband never worn.
    const failed = {
      ...report,
      retrieved: { cognitive: false, face: true, sessions: true },
      averages: { ...report.averages, focus: null, stress: null },
      highlights: { ...report.highlights, highest_stress: null, lowest_focus: null },
      sample_counts: { ...report.sample_counts, cognitive: 0 },
    }
    render(<WeeklySignalReport report={failed} />)
    expect(metric('Focus').getByText('Unavailable')).toBeInTheDocument()
    expect(metric('Lowest focus reading').getByText('Unavailable')).toBeInTheDocument()
    expect(screen.queryByText('No sensor', VISIBLE)).not.toBeInTheDocument()
  })

  it('still says Off since when the EEG read failed and the channel was withdrawn', () => {
    // The revocation comes from the consent read, which succeeded.
    const failed = {
      ...report,
      eeg_enabled: false,
      eeg_revoked_at: '2026-09-03T10:00:00Z',
      retrieved: { cognitive: false, face: true, sessions: true },
      averages: { ...report.averages, focus: null, stress: null },
      highlights: { ...report.highlights, highest_stress: null, lowest_focus: null },
      sample_counts: { ...report.sample_counts, cognitive: 0 },
    }
    render(<WeeklySignalReport report={failed} />)
    expect(metric('Focus').getByText(/^Off since/)).toBeInTheDocument()
    expect(metric('Lowest focus reading').getByText(/^Off since/)).toBeInTheDocument()
    expect(screen.queryByText('Unavailable', VISIBLE)).not.toBeInTheDocument()
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

  it('explains an unread signal day even when no sessions were cut', () => {
    // A failed rollup read past expiry: unrelated to `truncated`, and otherwise a silent quiet gap.
    render(<WeeklySignalReport report={{
      ...report,
      truncated: false,
      daily: [
        { date: '2026-07-15', focus: null, stress: null, cognitive_retrieved: false, face_retrieved: true },
        ...report.daily,
      ],
    }} />)
    expect(screen.getByText(/1 day is shown as a gap because the data could not be retrieved/i)).toBeInTheDocument()
    expect(screen.queryByText(/retrieval limit/i)).not.toBeInTheDocument()
  })

  describe('when the sessions read was cut', () => {
    // `truncated` is the sessions read alone; the signal figures are whole-week aggregates.
    const OLD_NOTE = /most recent samples only/i
    const cut = (sessionsRecorded, rowsRead, extra = {}) => buildWeeklyReport({
      truncated: true,
      sessions_recorded: sessionsRecorded,
      sample_counts: { ...buildWeeklyReport().sample_counts, sessions: rowsRead },
      ...extra,
    })

    // The note sits under the Sessions tile: the signal figures are whole-week aggregates.
    const sessionsBox = () => screen.getByRole('group', { name: 'Sessions' }).parentElement

    it('says the Sessions total is still exact when the count came back', () => {
      render(<WeeklySignalReport report={cut(137, 100)} />)
      expect(within(sessionsBox()).getByText(/this total is still the full count/i)).toBeInTheDocument()
      expect(screen.queryByText(/counts only the sessions read/i)).not.toBeInTheDocument()
      expect(screen.queryByText(OLD_NOTE)).not.toBeInTheDocument()
    })

    it('says the Sessions total counts only the sessions read when no count came back', () => {
      // Without the count the backend reports the rows it read, so the two are equal.
      render(<WeeklySignalReport report={cut(100, 100)} />)
      expect(within(sessionsBox()).getByText(/this total counts only the sessions read/i)).toBeInTheDocument()
      expect(screen.queryByText(/still the full count/i)).not.toBeInTheDocument()
      expect(screen.queryByText(OLD_NOTE)).not.toBeInTheDocument()
    })

    it('does not call a day that lost only its session count a gap', () => {
      // Per-day session counts are not drawn on this chart; that day's signals are whole.
      render(<WeeklySignalReport report={cut(137, 100, {
        daily: [{ date: '2026-07-15', focus: 0.7, stress: 0.3,
                  cognitive_retrieved: true, face_retrieved: true, heart_retrieved: true,
                  sessions: null, sessions_retrieved: false }],
      })} />)
      expect(screen.queryByText(/shown as a gap/i)).not.toBeInTheDocument()
    })

    it('says nothing about a limit when nothing was cut', () => {
      render(<WeeklySignalReport report={buildWeeklyReport({ truncated: false })} />)
      expect(screen.queryByText(/retrieval limit/i)).not.toBeInTheDocument()
      expect(screen.queryByText(OLD_NOTE)).not.toBeInTheDocument()
    })
  })

  it('renders without data', () => {
    render(<WeeklySignalReport report={null} />)
    expect(screen.getByText(/no readings this week yet/i)).toBeInTheDocument()
  })

  it('names a failed read in the tiles it affects, not elsewhere', () => {
    // A swallowed read leaves N/A and a dash, which alone read as "nothing recorded".
    const broken = {
      ...report,
      retrieved: { cognitive: false, face: true, sessions: true },
    }
    render(<WeeklySignalReport report={broken} />)
    expect(metric('Focus').getByText(/headband readings could not be loaded/i)).toBeInTheDocument()
    expect(metric('Calm').getByText(/headband readings could not be loaded/i)).toBeInTheDocument()
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
    expect(screen.getByText(/this week's readings could not be loaded/i)).toBeInTheDocument()
    expect(screen.queryByText(/no readings this week yet/i)).not.toBeInTheDocument()
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
    expect(metric('Calm').getByText('69%')).toBeInTheDocument()
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
    expect(metric('Calm').getByText('Calibrating')).toBeInTheDocument()
    expect(screen.queryByText('Engagement', VISIBLE)).not.toBeInTheDocument()
  })

  it('says Off since <date> when EEG consent was withdrawn', () => {
    render(<LiveSignalSummary report={{
      eeg_enabled: false,
      eeg_revoked_at: '2026-08-05T09:00:00Z',
      sample_counts: { cognitive: 0 },
    }} />)
    for (const tile of ['Focus', 'Calm']) {
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
    expect(metric('Focus').getByText('72%')).toBeInTheDocument()
    expect(metric('Calm').getByText('69%')).toBeInTheDocument()
  })

  it('still reports face data when the flag is absent', () => {
    const { face_included, ...legacy } = faceOff
    render(<WeeklySignalReport report={legacy} />)
    expect(metric('Dominant Emotion').getByText('neutral')).toBeInTheDocument()
    expect(metric('Dominant Emotion').queryByText('Not recorded')).not.toBeInTheDocument()
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
  expect(metric('Heart rate').getByText('72 bpm')).toBeInTheDocument()
  expect(metric('Heart rate').queryByText(/\d%/, VISIBLE)).not.toBeInTheDocument()
  expect(metric('Heart-rate variability').getByText('42 ms')).toBeInTheDocument()
  expect(screen.queryByText(/7240/)).not.toBeInTheDocument()
  // One name for one measure: never "RMSSD" or "HRV" on screen.
  expect(screen.queryByText(/RMSSD|HRV/, VISIBLE)).not.toBeInTheDocument()
})

test('the heart tiles are absent entirely when the channel was not read', () => {
  // Rather than a row of N/A, indistinguishable from a headband recording nothing.
  render(<WeeklySignalReport report={{ ...heartReport, heart_included: false }} />)

  expect(screen.queryByRole('group', { name: 'Heart rate' })).not.toBeInTheDocument()
  expect(screen.queryByRole('group', { name: 'Body arousal (heart rate)' })).not.toBeInTheDocument()
})

test('a payload from before the split does not claim the heart channel is off', () => {
  // No heart_included at all: nothing true to say about the channel.
  render(<WeeklySignalReport report={report} />)

  expect(screen.queryByRole('group', { name: 'Heart rate' })).not.toBeInTheDocument()
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

  // The note sits in the tile it qualifies.
  expect(metric('Heart rate').getByText(/none met the quality threshold/i)).toBeInTheDocument()
  expect(metric('Heart rate').getByText('Calibrating')).toBeInTheDocument()
  expect(metric('Heart-rate variability').getByText('Calibrating')).toBeInTheDocument()
})

test('a failed consent read is not rendered as a refusal', () => {
  render(<WeeklySignalReport report={{
    ...heartReport, consent_retrieved: false, heart_included: false, emotion_included: false,
  }} />)

  expect(screen.getByText(/Consent settings could not be read/i)).toBeInTheDocument()
})

test('a failed heart read is named in the heart tile, and is not "No sensor"', () => {
  render(<WeeklySignalReport report={{
    ...heartReport,
    highlights: { ...heartReport.highlights, heart_rate_bpm: null, rmssd_ms: null },
    sample_counts: { ...heartReport.sample_counts, heart: 0 },
    retrieved: { cognitive: true, face: true, heart: false, sessions: true },
  }} />)

  expect(metric('Heart rate').getByText(/heart readings could not be loaded/i)).toBeInTheDocument()
  expect(metric('Heart rate').getByText('Unavailable')).toBeInTheDocument()
  expect(metric('Heart rate').queryByText('No sensor')).not.toBeInTheDocument()
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

  expect(metric('Heart rate').getByText('68 bpm')).toBeInTheDocument()
  expect(metric('Heart rate').queryByText(/\d%/, VISIBLE)).not.toBeInTheDocument()
})

test('the live snapshot omits heart rather than showing an empty tile', () => {
  // The backend leaves the key out when unread.
  render(<LiveSignalSummary report={{
    ...heartReport,
    latest: { cognitive: { focus: 0.7 }, face: { attention: 0.8 } },
  }} />)

  expect(screen.queryByRole('group', { name: 'Heart rate' })).not.toBeInTheDocument()
})


describe('per-channel off states', () => {
  // Never render "no data" for something that was not recorded.
  const base = { ...report, face_included: false, emotion_included: false,
                 consent_retrieved: true }

  it('says when a channel was switched off', () => {
    render(<WeeklySignalReport report={{ ...base, emotion_revoked_at: '2026-08-03T09:00:00Z' }} />)
    expect(metric('Dominant Emotion').getByText((t) => /^Off since /.test(t) && t.includes('Aug')))
      .toBeInTheDocument()
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
    // The tile's own value must be null, or there is nothing for the reason to replace.
    const on = { ...report, emotion_included: true, face_included: true,
                 consent_retrieved: true,
                 highlights: { ...report.highlights, dominant_emotion: null } }

    // Readings arrived, none usable: calibrating, not absent.
    render(<WeeklySignalReport report={{ ...on, sample_counts: { face: 12 } }} />)
    expect(metric('Dominant Emotion').getByText('Calibrating')).toBeInTheDocument()
    cleanup()

    // Nothing arrived at all.
    render(<WeeklySignalReport report={{ ...on, sample_counts: { face: 0 } }} />)
    expect(metric('Dominant Emotion').getByText('No sensor')).toBeInTheDocument()
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
    expect(screen.queryByRole('group', { name: 'Heart rate' })).not.toBeInTheDocument()
    expect(screen.queryByText(/^Heart$/)).not.toBeInTheDocument()
  })
})

// ── the charts, for anyone who cannot see them ──────────────────────────────

// One small chart per unit: percent (focus and calm), heart rate, variability, body arousal.
const DAILY = /focus and calm day by day/i

describe('chart accessibility', () => {
  it('gives each chart a name that says what it shows', () => {
    render(<WeeklySignalReport report={report} />)
    const chart = screen.getByRole('img', { name: DAILY })
    expect(chart).toHaveAccessibleName(/focus/i)
  })

  it('states each series as a range rather than only naming it', () => {
    render(<WeeklySignalReport report={report} />)
    expect(screen.getByRole('img', { name: DAILY }))
      .toHaveAccessibleName(/Focus 70% to 74%/i)
  })

  it('draws no heart chart at all when heart was not read', () => {
    render(<WeeklySignalReport report={report} />)
    expect(screen.queryByRole('img', { name: /heart rate/i })).not.toBeInTheDocument()
  })

  it('never puts percent and bpm on one chart', () => {
    // One axis per chart: a dual axis makes two lines' crossings look meaningful.
    render(<WeeklySignalReport report={{ ...report, heart_included: true,
      daily: report.daily.map(d => ({ ...d, heart_rate_bpm: 70 })) }} />)
    expect(screen.getByRole('img', { name: DAILY })).not.toHaveAccessibleName(/bpm/)
    expect(screen.getByRole('img', { name: /^heart rate day by day/i })).not.toHaveAccessibleName(/%/)
  })

  it('keeps the data table out of the role="img" subtree', () => {
    // ARIA prunes roles inside an `img` but jsdom does not, so assert structure.
    render(<WeeklySignalReport report={report} />)
    const chart = screen.getByRole('img', { name: DAILY })
    const table = screen.getByRole('table', { name: DAILY })
    expect(chart).not.toContainElement(table)
  })

  it('carries the days themselves, not just the summary', () => {
    render(<WeeklySignalReport report={report} />)
    const table = screen.getByRole('table', { name: DAILY })
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
    const table = screen.getByRole('table', { name: /^heart rate day by day/i })
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
    screen.getByRole('img', { name: DAILY }).getAttribute('aria-label')

  it('reports percentages as percentages, not as the 0..1 they arrive as', () => {
    // Rows are scaled on the way in; a column without it announces 0..1.
    render(<WeeklySignalReport report={RATIOS} />)
    // Positive matches: an unscaled range can collapse to "1%" and dodge a negative one.
    expect(trendName()).toMatch(/Focus 70% to 74%/)
    // Calm is stress the right way up: 0.3 and 0.32 are 70% and 68%.
    expect(trendName()).toMatch(/Calm 68% to 70%/)
    expect(trendName()).not.toMatch(/stress/i)
  })

  it('does not describe a series the chart does not draw', () => {
    // `engagement` has no `<Line>` on this chart.
    render(<WeeklySignalReport report={RATIOS} />)
    expect(trendName()).not.toMatch(/engagement/i)
    expect(screen.getByRole('table', { name: DAILY }))
      .not.toHaveTextContent(/Engagement/i)
  })

  it('leaves heart out of the description when the line is not drawn', () => {
    // The `<Line>` is conditional on consent, so the description has to be.
    render(<WeeklySignalReport report={{ ...RATIOS, heart_included: false }} />)
    expect(trendName()).not.toMatch(/heart rate/i)
    expect(screen.queryByRole('img', { name: /heart rate/i })).not.toBeInTheDocument()
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

  const WEEKLY = /focus and calm week by week/i

  it('scales the ratio series and leaves heart rate in bpm, each on its own chart', () => {
    // The sr-only table is the only place the units are stated.
    render(<SignalTrend trend={trend} />)

    expect(within(screen.getByRole('table', { name: WEEKLY })).getByText('62%')).toBeInTheDocument()
    expect(within(screen.getByRole('table', { name: /^heart rate week by week/i }))
      .getByText('74 bpm')).toBeInTheDocument()
  })

  it('counts the weeks that recorded something, not the weeks in range', () => {
    // Coverage goes in the caption: a column must name a series the chart draws.
    render(<SignalTrend trend={trend} />)

    expect(screen.getByText(/2 of 3 have readings/i)).toBeInTheDocument()
  })

  it('leaves a week with nothing recorded as a gap', () => {
    render(<SignalTrend trend={trend} />)

    const row = within(screen.getByRole('table', { name: WEEKLY })).getByRole('row', { name: /06-01/ })
    expect(within(row).getAllByText(/not recorded/i).length).toBeGreaterThan(0)
  })

  it('says a failed read failed, rather than that nothing was recorded', () => {
    render(<SignalTrend trend={{ weeks: [], retrieved: false }} />)

    expect(screen.getByText(/could not be loaded/i)).toBeInTheDocument()
    expect(screen.queryByText(/no readings yet/i)).not.toBeInTheDocument()
  })

  it('says a genuinely empty history is empty', () => {
    render(<SignalTrend trend={{ weeks: [], retrieved: true }} />)

    expect(screen.getByText(/no readings yet/i)).toBeInTheDocument()
  })

  it('omits every heart series when the channel is off', () => {
    // Asserted on column and toggle: bare text matches both.
    render(<SignalTrend trend={{ ...trend, heart_included: false }} />)

    for (const name of [/^heart rate$/i, /variability/i, /body arousal/i]) {
      expect(screen.queryByRole('columnheader', { name })).not.toBeInTheDocument()
      expect(screen.queryByRole('switch', { name })).not.toBeInTheDocument()
    }
    expect(screen.getByRole('columnheader', { name: /focus/i })).toBeInTheDocument()
  })

  it('plots calm the right way up, from the stress the payload carries', () => {
    render(<SignalTrend trend={trend} />)
    // Week 1's stress 0.30 is calm 70%.
    const row = within(screen.getByRole('table', { name: WEEKLY })).getByRole('row', { name: /05-25/ })
    expect(within(row).getByText('70%')).toBeInTheDocument()
  })

  it('plots body arousal as a percentage, and leaves a week without it as a gap', () => {
    render(<SignalTrend trend={{ ...trend, weeks: trend.weeks.map((w, i) => (
      { ...w, body_arousal: i === 0 ? 0.25 : null })) }} />)
    const table = screen.getByRole('table', { name: /^body arousal week by week/i })
    expect(within(within(table).getByRole('row', { name: /05-25/ })).getByText('25%')).toBeInTheDocument()
    expect(within(within(table).getByRole('row', { name: /06-08/ })).getByText(/not recorded/i))
      .toBeInTheDocument()
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
    expect(screen.getByRole('columnheader', { name: /^heart rate$/i })).toBeInTheDocument()

    await userEvent.click(screen.getByRole('switch', { name: /^heart rate$/i }))

    expect(screen.queryByRole('columnheader', { name: /^heart rate$/i })).not.toBeInTheDocument()
    expect(screen.getByRole('columnheader', { name: /focus/i })).toBeInTheDocument()
    expect(screen.getByRole('columnheader', { name: /^calm$/i })).toBeInTheDocument()
  })

  it('says so on the toggle, not only by colour', async () => {
    render(<SignalTrend trend={trend} />)
    const heart = screen.getByRole('switch', { name: /^heart rate$/i })
    expect(heart).toHaveAttribute('aria-checked', 'true')

    await userEvent.click(heart)

    expect(screen.getByRole('switch', { name: /^heart rate$/i }))
      .toHaveAttribute('aria-checked', 'false')
  })

  it('takes any combination, not one at a time', async () => {
    render(<SignalTrend trend={trend} />)

    await userEvent.click(screen.getByRole('switch', { name: /focus/i }))
    await userEvent.click(screen.getByRole('switch', { name: /^heart rate$/i }))

    expect(screen.queryByRole('columnheader', { name: /focus/i })).not.toBeInTheDocument()
    expect(screen.queryByRole('columnheader', { name: /^heart rate$/i })).not.toBeInTheDocument()
    expect(screen.getByRole('columnheader', { name: /^calm$/i })).toBeInTheDocument()
  })

  it('puts a measurement back', async () => {
    render(<SignalTrend trend={trend} />)

    await userEvent.click(screen.getByRole('switch', { name: /^calm$/i }))
    expect(screen.queryByRole('columnheader', { name: /^calm$/i })).not.toBeInTheDocument()

    await userEvent.click(screen.getByRole('switch', { name: /^calm$/i }))
    expect(screen.getByRole('columnheader', { name: /^calm$/i })).toBeInTheDocument()
  })

  it('explains an empty chart and offers a way back, rather than refusing the last click', async () => {
    // A claim about the view, distinct from the claims about the data.
    render(<SignalTrend trend={trend} />)
    for (const name of [/focus/i, /^calm$/i, /^heart rate$/i, /variability/i, /body arousal/i]) {
      await userEvent.click(screen.getByRole('switch', { name }))
    }

    expect(screen.getByText(/no measurements selected/i)).toBeInTheDocument()
    expect(screen.queryByText(/no readings yet/i)).not.toBeInTheDocument()
    // The chart is not rendered at all, so no empty table.
    expect(screen.queryByRole('table')).not.toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: /show all/i }))
    expect(screen.getByRole('columnheader', { name: /focus/i })).toBeInTheDocument()
  })

  it('draws a measurement that only becomes available later', () => {
    // The hook stores what is *hidden*, so a series that arrives later is shown.
    const { rerender } = render(<SignalTrend trend={{ ...trend, heart_included: false }} />)
    expect(screen.queryByRole('columnheader', { name: /^heart rate$/i })).not.toBeInTheDocument()

    rerender(<SignalTrend trend={trend} />)

    expect(screen.getByRole('columnheader', { name: /^heart rate$/i })).toBeInTheDocument()
    expect(screen.getByRole('switch', { name: /^heart rate$/i })).toHaveAttribute('aria-checked', 'true')
  })

  it('keeps a hidden measurement hidden across a re-render', async () => {
    const { rerender } = render(<SignalTrend trend={trend} />)
    await userEvent.click(screen.getByRole('switch', { name: /^calm$/i }))

    rerender(<SignalTrend trend={{ ...trend, retrieved: true }} />)

    expect(screen.queryByRole('columnheader', { name: /^calm$/i })).not.toBeInTheDocument()
  })

  it('paints each swatch with the colour its own line is stroked with', () => {
    // The swatch is aria-hidden and jsdom has no stylesheet, so only the inline style is checkable.
    render(<SignalTrend trend={trend} />)

    for (const name of [/^focus$/i, /^calm$/i, /^heart rate$/i]) {
      const swatch = screen.getByRole('switch', { name }).querySelector('[aria-hidden="true"]')
      expect(swatch.getAttribute('style')).toMatch(/#[0-9a-f]{6}|rgb\(/i)
      expect(swatch.getAttribute('style')).not.toMatch(/undefined/)
    }
    // The heart line's palette value, shared with SessionReview.
    expect(screen.getByRole('switch', { name: /^heart rate$/i })
      .querySelector('[aria-hidden="true"]')).toHaveStyle({ backgroundColor: '#a855f7' })
  })

  it('keeps the swatch colour when the measurement is switched off', async () => {
    // Hollow rather than gone: the chip still names its line.
    render(<SignalTrend trend={trend} />)
    await userEvent.click(screen.getByRole('switch', { name: /^heart rate$/i }))

    const swatch = screen.getByRole('switch', { name: /^heart rate$/i })
      .querySelector('[aria-hidden="true"]')
    expect(swatch.getAttribute('style')).toContain('a855f7')
    expect(swatch.getAttribute('style')).not.toMatch(/undefined/)
  })

  it('offers the same control on the daily chart', async () => {
    render(<WeeklySignalReport report={report} />)

    await userEvent.click(screen.getByRole('switch', { name: /^focus$/i }))

    expect(screen.queryByRole('columnheader', { name: /focus/i })).not.toBeInTheDocument()
    expect(screen.getByRole('columnheader', { name: /^calm$/i })).toBeInTheDocument()
  })
})

test('an erased heart channel says it was erased, not that there is no sensor', () => {
  // The run: a parent erased headband heart; consent stayed on and no heart rows remained.
  const erased = { ...heartReport, highlights: { ...heartReport.highlights, heart_rate_bpm: null, rmssd_ms: null },
                   sample_counts: { ...heartReport.sample_counts, heart: 0 },
                   heart_erased_at: '2026-10-07T00:40:00Z' }
  render(<WeeklySignalReport report={erased} />)

  expect(metric('Heart rate').getByText(/^Erased /)).toBeInTheDocument()
  expect(screen.queryByText('No sensor')).not.toBeInTheDocument()
})

test('readings recorded after an erasure are shown, not the erasure', () => {
  render(<WeeklySignalReport report={{ ...heartReport, heart_erased_at: '2026-10-01T00:00:00Z' }} />)
  expect(metric('Heart rate').getByText('72 bpm')).toBeInTheDocument()
})

// ── compared with the student's own usual, and body arousal ─────────────────

describe("each tile against the student's usual", () => {
  it('shows the verdict and the usual range on the tile it belongs to', () => {
    render(<WeeklySignalReport report={buildWeeklyReport({ usual: { measures: {
      focus: buildUsualMeasure({ verdict: 'higher' }),
      calm: buildUsualMeasure({ status: 'not_enough_history', verdict: null, low: null, high: null }),
    } } })} />)
    expect(metric('Focus').getByText('Higher than usual')).toBeInTheDocument()
    expect(metric('Focus').getByText('Usual 55–65%')).toBeInTheDocument()
    expect(metric('Calm').getByText('Not enough history yet')).toBeInTheDocument()
    expect(metric('Calm').queryByText(/^Usual/)).not.toBeInTheDocument()
  })

  it('draws no comparison for a payload without `usual`', () => {
    render(<WeeklySignalReport report={buildWeeklyReport()} />)
    expect(screen.queryByText(/than usual|About usual|^Usual/, VISIBLE)).not.toBeInTheDocument()
  })
})

describe('body arousal', () => {
  const ON = { usual: undefined }
  it('shows a measured share as a percentage', () => {
    render(<WeeklySignalReport report={buildWeeklyReport(ON)} />)
    expect(metric('Body arousal (heart rate)').getByText('22%')).toBeInTheDocument()
  })

  it.each([
    ['camera_only', 'Headband only'],
    ['pending', 'Lesson in progress'],
    ['calibrating', 'Calibrating'],
    ['unusable', 'Readings too unsteady'],
    ['none', 'No headband readings'],
    ['unknown', "Couldn't check"],
    ['not_retrieved', "Couldn't load"],
  ])('says why a %s week has no figure, never 0%%', (state, words) => {
    render(<WeeklySignalReport report={buildWeeklyReport({
      body_arousal: buildBodyArousal({ state, high_share: null, classified_windows: 0 }) })} />)
    expect(metric('Body arousal (heart rate)').getByText(words, VISIBLE)).toBeInTheDocument()
    expect(metric('Body arousal (heart rate)').queryByText('0%')).not.toBeInTheDocument()
  })

  it('says a thin share is rough, in its own tile', () => {
    render(<WeeklySignalReport report={buildWeeklyReport({
      body_arousal: buildBodyArousal({ few_readings: true }) })} />)
    expect(metric('Body arousal (heart rate)').getByText(/only a few readings/i)).toBeInTheDocument()
  })

  it('says a running lesson is not counted yet, only when one is', () => {
    const { unmount } = render(<WeeklySignalReport report={buildWeeklyReport({
      body_arousal: buildBodyArousal({ pending: true }) })} />)
    expect(metric('Body arousal (heart rate)').getByText(/still in progress is not counted/i))
      .toBeInTheDocument()
    unmount()
    // `null` is "could not check": not a claim that a lesson is running.
    render(<WeeklySignalReport report={buildWeeklyReport({
      body_arousal: buildBodyArousal({ pending: null }) })} />)
    expect(screen.queryByText(/still in progress/i)).not.toBeInTheDocument()
  })

  it.each([
    ['pending', 'Lesson in progress'],
    ['calibrating', 'Calibrating'],
    ['not_retrieved', "Couldn't load"],
  ])("names a %s day's reason in the chart table, not 'not recorded'", (state, words) => {
    render(<WeeklySignalReport report={buildWeeklyReport({ daily: [
      { date: '2026-07-20', focus: 0.7, stress: 0.3, heart_rate_bpm: 70, body_arousal: 0.2, body_arousal_state: 'measured' },
      { date: '2026-07-21', focus: 0.7, stress: 0.3, heart_rate_bpm: 70, body_arousal: null, body_arousal_state: state },
    ] })} />)
    const table = screen.getByRole('table', { name: /^body arousal/i })
    expect(within(within(table).getByRole('row', { name: /07-20/ })).getByText('20%')).toBeInTheDocument()
    expect(within(within(table).getByRole('row', { name: /07-21/ })).getByText(words)).toBeInTheDocument()
  })

  it('marks a day resting on few readings as rough, in the table and under the chart', () => {
    const day = (date, few) => ({ date, focus: 0.7, stress: 0.3, heart_rate_bpm: 70, body_arousal: 0.2,
      body_arousal_state: 'measured', body_arousal_few_readings: few })
    const { unmount } = render(<WeeklySignalReport report={buildWeeklyReport({
      daily: [day('2026-07-20', false), day('2026-07-21', true)] })} />)
    const table = screen.getByRole('table', { name: /^body arousal/i })
    expect(within(within(table).getByRole('row', { name: /07-20/ })).getByText('20%')).toBeInTheDocument()
    expect(within(within(table).getByRole('row', { name: /07-21/ })).getByText('20% (few readings)'))
      .toBeInTheDocument()
    expect(screen.getByText(/hollow points rest on only a few readings/i)).toBeInTheDocument()
    unmount()
    render(<WeeklySignalReport report={buildWeeklyReport({
      daily: [day('2026-07-20', false), day('2026-07-21', false)] })} />)
    expect(screen.queryByText(/hollow points/i)).not.toBeInTheDocument()
  })

  it('marks a rough week in the week-by-week table', () => {
    render(<SignalTrend trend={{ retrieved: true, heart_included: true, weeks: [
      { week_start: '2026-06-01', focus: 0.6, stress: 0.3, days_with_data: 3, body_arousal: 0.4,
        body_arousal_state: 'measured', body_arousal_few_readings: true },
    ] }} />)
    const table = screen.getByRole('table', { name: /^body arousal week by week/i })
    expect(within(table).getByText('40% (few readings)')).toBeInTheDocument()
    expect(screen.getByText(/hollow points/i)).toBeInTheDocument()
  })

  it("names a week's reason in the week-by-week table", () => {
    render(<SignalTrend trend={{ retrieved: true, heart_included: true, weeks: [
      { week_start: '2026-06-01', focus: 0.6, stress: 0.3, days_with_data: 3, body_arousal: null, body_arousal_state: 'unknown' },
    ] }} />)
    const table = screen.getByRole('table', { name: /^body arousal week by week/i })
    expect(within(table).getByText("Couldn't check")).toBeInTheDocument()
  })

  it('reads a day with no heart readings as "not recorded", like the rest of its row', () => {
    render(<WeeklySignalReport report={buildWeeklyReport({ daily: [
      { date: '2026-07-20', focus: 0.7, stress: 0.3, heart_rate_bpm: 70, body_arousal: 0.2, body_arousal_state: 'measured' },
      { date: '2026-07-21', focus: null, stress: null, heart_rate_bpm: null, body_arousal: null, body_arousal_state: 'none' },
    ] })} />)
    const row = within(screen.getByRole('table', { name: /^body arousal/i })).getByRole('row', { name: /07-21/ })
    expect(within(row).getByText('not recorded')).toBeInTheDocument()
    expect(within(row).queryByText('No headband readings')).not.toBeInTheDocument()
  })

  it('counts weeks and days in words, never "week ofs"', () => {
    const weeks = [
      { week_start: '2026-06-01', focus: 0.6, stress: 0.3, days_with_data: 3 },
      { week_start: '2026-06-08', focus: 0.62, stress: 0.3, days_with_data: 3 },
    ]
    render(<SignalTrend trend={{ retrieved: true, weeks }} />)
    expect(screen.getByRole('img', { name: /^focus and calm week by week over 2 weeks\./i })).toBeInTheDocument()
    cleanup()
    render(<WeeklySignalReport report={buildWeeklyReport({ daily: [
      { date: '2026-07-20', focus: 0.7, stress: 0.3 }, { date: '2026-07-21', focus: 0.7, stress: 0.3 },
    ] })} />)
    expect(screen.getByRole('img', { name: /^focus and calm day by day over 2 days\./i })).toBeInTheDocument()
  })

  it('is never shown beside the EEG calm under one "stress" label', () => {
    render(<WeeklySignalReport report={buildWeeklyReport()} />)
    expect(screen.queryByText(/stress/i, VISIBLE)).not.toBeInTheDocument()
  })
})

describe('ChartSummaryPanel and "Hide sensor data"', () => {
  const summary = ['Answered 30 of 40.', 'Average focus is 60%.', 'Average calm is 55%.', 'Topics: ordering.']

  it('drops exactly the sensor sentences when asked, and renumbers the rest', () => {
    render(<ChartSummaryPanel summary={summary} sensorLines={[1, 2]} hideSensorLines
                              onGenerate={() => {}} />)
    expect(screen.queryByText(/Average focus/)).not.toBeInTheDocument()
    expect(screen.queryByText(/Average calm/)).not.toBeInTheDocument()
    expect(screen.getByText('Topics: ordering.')).toBeInTheDocument()
    expect(screen.getByText('2')).toBeInTheDocument()
    expect(screen.queryByText('4')).not.toBeInTheDocument()
  })

  it('leaves hidden sensor reads out of its "couldn\'t be loaded" note', () => {
    const retrieved = { signals: false, trend: false, stats: false, topics: true }
    const { unmount } = render(<ChartSummaryPanel summary={summary} sensorLines={[1, 2]} hideSensorLines
                                                  retrieved={retrieved} onGenerate={() => {}} />)
    const note = screen.getByText(/couldn’t be loaded/)
    expect(note).toHaveTextContent('the practice totals')
    expect(note).not.toHaveTextContent(/signal averages|term trend/)
    unmount()
    // Shown, they are named.
    render(<ChartSummaryPanel summary={summary} sensorLines={[1, 2]} retrieved={retrieved} onGenerate={() => {}} />)
    expect(screen.getByText(/couldn’t be loaded/)).toHaveTextContent(/signal averages, the term trend, the practice totals/)
  })

  it('says nothing failed when only hidden sensor reads did', () => {
    render(<ChartSummaryPanel summary={summary} sensorLines={[1, 2]} hideSensorLines
                              retrieved={{ signals: false, trend: false, stats: true, topics: true }}
                              onGenerate={() => {}} />)
    expect(screen.queryByText(/couldn’t be loaded/)).not.toBeInTheDocument()
  })

  it('shows every sentence when sensor data is not hidden', () => {
    render(<ChartSummaryPanel summary={summary} sensorLines={[1, 2]} onGenerate={() => {}} />)
    expect(screen.getByText(/Average focus/)).toBeInTheDocument()
  })
})
