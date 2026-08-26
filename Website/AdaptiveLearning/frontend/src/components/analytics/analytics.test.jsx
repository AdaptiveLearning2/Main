import { render, screen } from '@testing-library/react'
import { describe, it, expect } from 'vitest'
import Heatmap from '../charts/Heatmap'
import Panel from './Panel'
import AlertFeed from './AlertFeed'
import ClassTopicHeatmap from './ClassTopicHeatmap'
import ClassAccuracyTrend from './ClassAccuracyTrend'
import ClassTimeOfDay from './ClassTimeOfDay'
import FocusAccuracy from './FocusAccuracy'
import ClassSignalTrend from './ClassSignalTrend'
import ClassSignalRoster from './ClassSignalRoster'

/**
 * The teacher analytics panels.
 *
 * Almost everything here is about the states a panel must keep apart. A chart
 * that renders an empty axis for a failed read is the failure this whole
 * section was written to avoid, and it is invisible: the page looks fine, and
 * it is telling a teacher that nobody in the class is working.
 */

const cell = (accuracy, attempted, correct) => ({ accuracy, attempted, correct })

// ─── the shared state ladder ───────────────────────────────────────────────

describe('Panel', () => {
  const CHILD = <p>the chart</p>

  it('shows a skeleton while loading, not an empty chart', () => {
    render(<Panel title="T" loading>{CHILD}</Panel>)
    expect(screen.getByRole('status', { name: /loading/i })).toBeInTheDocument()
    expect(screen.queryByText('the chart')).not.toBeInTheDocument()
  })

  it('distinguishes a failed read from a quiet week', () => {
    const { rerender } = render(
      <Panel title="T" failed what="the trend" empty emptyNote="Nothing yet.">{CHILD}</Panel>)
    expect(screen.getByText(/couldn't load the trend/i)).toBeInTheDocument()
    expect(screen.queryByText('Nothing yet.')).not.toBeInTheDocument()

    // Same panel, read succeeded, genuinely nothing recorded.
    rerender(<Panel title="T" empty emptyNote="Nothing yet.">{CHILD}</Panel>)
    expect(screen.getByText('Nothing yet.')).toBeInTheDocument()
    expect(screen.queryByText(/couldn't load/i)).not.toBeInTheDocument()
  })

  it('a failure outranks an empty payload', () => {
    // A failed aggregate answers 200 with an empty default payload, so both
    // flags arrive true together. Rendering "nothing recorded" there is the
    // exact lie the flag exists to prevent.
    render(<Panel title="T" failed empty what="the trend" emptyNote="Nothing yet.">{CHILD}</Panel>)
    expect(screen.getByText(/couldn't load the trend/i)).toBeInTheDocument()
  })
})

// ─── the heatmap primitive ─────────────────────────────────────────────────

describe('Heatmap', () => {
  const COLUMNS = [{ key: 1, label: 'algebra' }, { key: 2, label: 'geometry' }]

  it('is a real table with row and column headers', () => {
    // Not an sr-only copy beside a picture: a matrix *is* a table, and a
    // reader can ask for one cell by its two headers.
    render(<Heatmap caption="c" rowHeader="Student" columns={COLUMNS} rows={[
      { key: 'a', label: 'Ada', cells: [cell(0.5, 10, 5), cell(1, 4, 4)] },
    ]} />)
    expect(screen.getByRole('table')).toBeInTheDocument()
    expect(screen.getByRole('columnheader', { name: /algebra/ })).toBeInTheDocument()
    expect(screen.getByRole('rowheader', { name: 'Ada' })).toBeInTheDocument()
  })

  it('reads a cell out with its denominator, not just a percentage', () => {
    // "75%" and "3 of 4 correct" are different facts, and the second is the
    // one that says whether to believe the first.
    render(<Heatmap caption="c" rowHeader="Student" columns={COLUMNS} rows={[
      { key: 'a', label: 'Ada', cells: [cell(0.75, 400, 300), null] },
    ]} />)
    expect(screen.getByLabelText(/Ada, algebra: 75%, 300 of 400 correct/)).toBeInTheDocument()
  })

  it('an unattempted topic is not the zero colour', () => {
    // A topic nobody was served is not a topic they failed, and colouring it
    // as the worst cell on the board says the opposite.
    render(<Heatmap caption="c" rowHeader="Student" columns={COLUMNS} rows={[
      { key: 'a', label: 'Ada', cells: [null, cell(0, 8, 0)] },
    ]} />)
    const missing = screen.getByLabelText(/Ada, algebra: not attempted/)
    const zero = screen.getByLabelText(/Ada, geometry: 0%/)
    expect(missing.className).not.toEqual(zero.className)
    expect(missing).toHaveTextContent('–')
    expect(zero).toHaveTextContent('0%')
  })

  it('marks a cell too thin to rely on, without hiding the figure', () => {
    render(<Heatmap caption="c" rowHeader="Student" minAttempts={4} columns={COLUMNS} rows={[
      { key: 'a', label: 'Ada', cells: [cell(1, 1, 1), cell(1, 40, 40)] },
    ]} />)
    expect(screen.getByLabelText(/algebra: 100%, 1 of 1 correct, too few attempts/))
      .toBeInTheDocument()
    // The confident cell carries no such warning.
    expect(screen.getByLabelText(/geometry: 100%, 40 of 40 correct$/)).toBeInTheDocument()
  })

  it('renders nothing rather than an empty grid', () => {
    const { container } = render(
      <Heatmap caption="c" rowHeader="Student" columns={[]} rows={[]} />)
    expect(container).toBeEmptyDOMElement()
  })
})

// ─── topic heatmap ─────────────────────────────────────────────────────────

describe('ClassTopicHeatmap', () => {
  const DATA = {
    retrieved: true,
    min_attempts: 4,
    topics: [
      { topic_id: 1, topic_name: 'algebra', accuracy: 0.5 },
      { topic_id: 2, topic_name: 'geometry', accuracy: null },
    ],
    students: [
      { user_id: 'a', name: 'Ada', cells: [cell(0.5, 10, 5), null] },
    ],
  }

  it('puts the class figure under each topic heading', () => {
    // A column that is red all the way down is a teaching problem, not several
    // students having a bad week, and the class figure is what says which.
    render(<ClassTopicHeatmap data={DATA} />)
    const header = screen.getByRole('columnheader', { name: /algebra/ })
    expect(header).toHaveTextContent('50% class')
  })

  it('says so when a topic has no attempts at all', () => {
    render(<ClassTopicHeatmap data={DATA} />)
    expect(screen.getByRole('columnheader', { name: /geometry/ }))
      .toHaveTextContent('no attempts')
  })

  it('a failed read is not a class that has answered nothing', () => {
    render(<ClassTopicHeatmap data={{ retrieved: false, topics: [], students: [] }} />)
    expect(screen.getByText(/couldn't load topic accuracy/i)).toBeInTheDocument()
  })

  it('an empty but successful read says nothing has been answered', () => {
    render(<ClassTopicHeatmap data={{ retrieved: true, topics: [], students: [] }} />)
    expect(screen.getByText(/no topics answered yet/i)).toBeInTheDocument()
  })
})

// ─── accuracy trend ────────────────────────────────────────────────────────

describe('ClassAccuracyTrend', () => {
  const DAYS = [
    { day: '2026-06-10', attempted: 10, correct: 7, accuracy: 0.7 },
    { day: '2026-06-11', attempted: 0, correct: 0, accuracy: null },
  ]

  it('describes the chart with the number of days that had data', () => {
    render(<ClassAccuracyTrend data={{ retrieved: true, days: DAYS, attempted: 10, timezone: 'UTC' }} />)
    expect(screen.getByRole('img', { name: /across 2 days, from 10 questions answered on 1 of them/ }))
      .toBeInTheDocument()
  })

  it('a day nobody answered reads as not recorded, never as zero', () => {
    // The sr-only table is where this shows. A 0% row would be a claim the
    // class answered and got everything wrong.
    render(<ClassAccuracyTrend data={{ retrieved: true, days: DAYS, attempted: 10, timezone: 'UTC' }} />)
    expect(screen.getByRole('table')).toHaveTextContent(/not recorded/i)
  })

  it('announces percentages as percentages', () => {
    // The rows are pre-scaled, so the column carries a `%` unit and no
    // `scale`. Combining the two would announce 7000%.
    render(<ClassAccuracyTrend data={{ retrieved: true, days: DAYS, attempted: 10, timezone: 'UTC' }} />)
    expect(screen.getByRole('img', { name: /Accuracy 70%/ })).toBeInTheDocument()
  })

  it('a failed read is not a quiet month', () => {
    render(<ClassAccuracyTrend data={{ retrieved: false, days: [] }} />)
    expect(screen.getByText(/couldn't load the accuracy trend/i)).toBeInTheDocument()
  })

  it('days in range with no answers at all is an empty state, not a flat line', () => {
    render(<ClassAccuracyTrend data={{
      retrieved: true, attempted: 0, timezone: 'UTC',
      days: [{ day: '2026-06-11', attempted: 0, correct: 0, accuracy: null }],
    }} />)
    expect(screen.getByText(/no questions answered in this range yet/i)).toBeInTheDocument()
  })
})

// ─── time of day ───────────────────────────────────────────────────────────

describe('ClassTimeOfDay', () => {
  const DATA = {
    retrieved: true, days: 30, attempted: 20, hours: [9, 14],
    cells: [
      { weekday: 0, hour: 9, attempted: 10, correct: 7, accuracy: 0.7 },
      { weekday: 2, hour: 14, attempted: 10, correct: 5, accuracy: 0.5 },
    ],
  }

  it('names weekdays and hours the way a teacher would', () => {
    render(<ClassTimeOfDay data={DATA} />)
    expect(screen.getByRole('rowheader', { name: 'Monday' })).toBeInTheDocument()
    expect(screen.getByRole('columnheader', { name: '9am' })).toBeInTheDocument()
    expect(screen.getByRole('columnheader', { name: '2pm' })).toBeInTheDocument()
  })

  it('only shows the days the class has worked', () => {
    // 168 cells of which a school uses thirty teaches a reader to skip it.
    render(<ClassTimeOfDay data={DATA} />)
    expect(screen.queryByRole('rowheader', { name: 'Saturday' })).not.toBeInTheDocument()
    expect(screen.getByRole('rowheader', { name: 'Wednesday' })).toBeInTheDocument()
  })

  it('an hour a given day never used is not attempted, not zero', () => {
    render(<ClassTimeOfDay data={DATA} />)
    expect(screen.getByLabelText(/Monday, 2pm: not attempted/)).toBeInTheDocument()
  })

  it('a failed read is not an empty timetable', () => {
    render(<ClassTimeOfDay data={{ retrieved: false, cells: [], hours: [] }} />)
    expect(screen.getByText(/couldn't load the time-of-day breakdown/i)).toBeInTheDocument()
  })
})

// ─── focus against accuracy ────────────────────────────────────────────────

describe('FocusAccuracy', () => {
  const BUCKETS = [
    { focus_low: 0, focus_high: 0.5, answered: 20, correct: 5, accuracy: 0.25 },
    { focus_low: 0.5, focus_high: 1, answered: 20, correct: 18, accuracy: 0.9 },
  ]

  it('withholds a correlation over too few answers but still draws the bars', () => {
    // r over a dozen answers is noise, and it renders as one objective-looking
    // number. The bars carry their own sample sizes, which is why they stay.
    render(<FocusAccuracy data={{
      retrieved: true, eeg_enabled: true, sufficient: false,
      correlation: null, pairs: 8, min_pairs: 30, buckets: BUCKETS,
    }} />)
    expect(screen.getByText(/too few answers.*8 of the 30 needed/i)).toBeInTheDocument()
    expect(screen.getByRole('img', { name: /from 8 answers with a focus reading/ }))
      .toBeInTheDocument()
  })

  it('reports a correlation in words as well as a coefficient', () => {
    render(<FocusAccuracy data={{
      retrieved: true, eeg_enabled: true, sufficient: true,
      correlation: 0.31, pairs: 400, min_pairs: 30, buckets: BUCKETS,
    }} />)
    expect(screen.getByText(/positive: a weak relationship \(r = 0\.31\) over 400 answers/i))
      .toBeInTheDocument()
  })

  it('an exact zero is not a negative relationship', () => {
    // `corr()` returns 0 whenever the two are perfectly unrelated, which is a
    // realistic answer rather than an edge case. `r > 0 ? … : 'Negative'`
    // labelled it "Negative: little or no relationship" — a sentence that
    // contradicts itself and points a teacher at a trend that is not there.
    render(<FocusAccuracy data={{
      retrieved: true, eeg_enabled: true, sufficient: true,
      correlation: 0, pairs: 400, min_pairs: 30, buckets: BUCKETS,
    }} />)
    expect(screen.getByText(/no direction: little or no relationship \(r = 0\.00\)/i))
      .toBeInTheDocument()
    expect(screen.queryByText(/negative/i)).not.toBeInTheDocument()
  })

  it('a coefficient that rounds to zero agrees with the figure printed beside it', () => {
    // 0.004 prints as `r = 0.00`, so a signed label would contradict the
    // number in the same sentence. The direction is decided on the rounded
    // value for exactly that reason.
    render(<FocusAccuracy data={{
      retrieved: true, eeg_enabled: true, sufficient: true,
      correlation: 0.004, pairs: 400, min_pairs: 30, buckets: BUCKETS,
    }} />)
    expect(screen.getByText(/no direction.*\(r = 0\.00\)/i)).toBeInTheDocument()
    expect(screen.queryByText(/positive/i)).not.toBeInTheDocument()
  })

  it('still names a direction when there is one', () => {
    // The negative tests above pass against a component that never says
    // "Positive" or "Negative" at all, so this is what makes them mean
    // something.
    render(<FocusAccuracy data={{
      retrieved: true, eeg_enabled: true, sufficient: true,
      correlation: -0.55, pairs: 400, min_pairs: 30, buckets: BUCKETS,
    }} />)
    expect(screen.getByText(/negative: a moderate relationship \(r = -0\.55\)/i))
      .toBeInTheDocument()
  })

  it('enough data with no computable coefficient is not too little data', () => {
    // `corr()` answers null when an input has no variance. Saying "too few
    // answers" there sends a teacher looking for the wrong problem.
    render(<FocusAccuracy data={{
      retrieved: true, eeg_enabled: true, sufficient: true,
      correlation: null, pairs: 400, min_pairs: 30, buckets: BUCKETS,
    }} />)
    expect(screen.getByText(/no coefficient could be computed from these 400 answers/i))
      .toBeInTheDocument()
    expect(screen.queryByText(/too few answers/i)).not.toBeInTheDocument()
  })

  it('a declined channel says the sensor is off, with the date', () => {
    render(<FocusAccuracy data={{
      retrieved: true, eeg_enabled: false, eeg_revoked_at: '2026-06-01T00:00:00Z',
      consent_retrieved: true, buckets: [],
    }} />)
    // The month and day are not pinned: `fmtDate` formats in the reader's own
    // locale and timezone, so a UTC midnight is the previous day for any
    // runner behind UTC. Pinning it would make this fail on a machine rather
    // than on a regression.
    expect(screen.getByText(/headband recording is off since \w+ \d+/i)).toBeInTheDocument()
  })

  it('a declined channel with no recorded date still says the sensor is off', () => {
    // `fmtDate` answers null for a missing or unparseable date, and the
    // sentence has to survive that rather than reading "off since ".
    render(<FocusAccuracy data={{
      retrieved: true, eeg_enabled: false, eeg_revoked_at: null,
      consent_retrieved: true, buckets: [],
    }} />)
    expect(screen.getByText(/headband recording is off, so no focus readings were used/i))
      .toBeInTheDocument()
  })

  it('an unreadable consent record is not a withdrawal', () => {
    // "They turned this off" is a claim about a decision. A failed read has
    // not earned it.
    render(<FocusAccuracy data={{
      retrieved: true, eeg_enabled: true, consent_retrieved: false, buckets: [],
    }} />)
    expect(screen.getByText(/unavailable/i)).toBeInTheDocument()
    expect(screen.queryByText(/recording is off/i)).not.toBeInTheDocument()
  })

  it('a failed read is not a student with no focus readings', () => {
    render(<FocusAccuracy data={{ retrieved: false, eeg_enabled: true, buckets: [] }} />)
    expect(screen.getByText(/couldn't load the focus comparison/i)).toBeInTheDocument()
  })

  it('carries the caveat that a relationship is not a cause', () => {
    render(<FocusAccuracy data={{
      retrieved: true, eeg_enabled: true, sufficient: true,
      correlation: 0.6, pairs: 400, min_pairs: 30, buckets: BUCKETS,
    }} />)
    expect(screen.getByText(/a relationship here is not a cause/i)).toBeInTheDocument()
  })
})

// ─── the alert feed ────────────────────────────────────────────────────────

describe('AlertFeed', () => {
  const alert = (kind, over = {}) => ({
    id: `a-${kind}`, kind, student_name: 'Ada', detail: {},
    created_at: '2026-06-11T09:30:00Z', school_day: '2026-06-11', ...over,
  })

  it('describes a timed-out session and says the work was kept', () => {
    // The reassurance is load-bearing: "session timed out" alone reads as
    // "their work was lost", which is the first thing a teacher would ask.
    render(<AlertFeed data={{
      retrieved: true, days: 7,
      alerts: [alert('session_auto_closed', { detail: { questions_answered: 12 } })],
    }} />)
    expect(screen.getByText(/Ada — Session timed out/)).toBeInTheDocument()
    expect(screen.getByText(/after 12 questions. The work was saved./))
      .toBeInTheDocument()
  })

  it('does not say "1 questions"', () => {
    render(<AlertFeed data={{
      retrieved: true, days: 7,
      alerts: [alert('session_auto_closed', { detail: { questions_answered: 1 } })],
    }} />)
    expect(screen.getByText(/after 1 question\./)).toBeInTheDocument()
  })

  it('survives a detail with no count rather than saying "undefined"', () => {
    render(<AlertFeed data={{
      retrieved: true, days: 7, alerts: [alert('session_auto_closed')],
    }} />)
    expect(screen.getByText(/Ended without being finished\. The work was saved\./))
      .toBeInTheDocument()
  })

  it('tells a teacher what to check when no readings arrived', () => {
    render(<AlertFeed data={{
      retrieved: true, days: 7, alerts: [alert('signals_missing')],
    }} />)
    expect(screen.getByText(/Ada — No headband data/)).toBeInTheDocument()
    expect(screen.getByText(/Check the headband is paired/)).toBeInTheDocument()
  })

  it('shows an unrecognised kind rather than dropping it', () => {
    // The CHECK constraint means this should be impossible. If it happens, a
    // visible unstyled row is what gets it reported; skipping it silently
    // would make a real alert invisible.
    render(<AlertFeed data={{
      retrieved: true, days: 7, alerts: [alert('something_new')],
    }} />)
    expect(screen.getByText(/Unrecognised alert \(something_new\)/)).toBeInTheDocument()
  })

  it('uses the school day from the payload, not the browser', () => {
    // Re-deriving it here would show a teacher marking from another timezone
    // the wrong day for the lesson.
    render(<AlertFeed data={{
      retrieved: true, days: 7,
      alerts: [alert('signals_missing', { school_day: '2026-06-10' })],
    }} />)
    expect(screen.getByText(/2026-06-10/)).toBeInTheDocument()
  })

  it('a quiet week is not a failed read', () => {
    const { rerender } = render(<AlertFeed data={{ retrieved: true, days: 7, alerts: [] }} />)
    expect(screen.getByText(/nothing to flag/i)).toBeInTheDocument()

    rerender(<AlertFeed data={{ retrieved: false, alerts: [] }} />)
    expect(screen.getByText(/couldn't load the alert feed/i)).toBeInTheDocument()
    expect(screen.queryByText(/nothing to flag/i)).not.toBeInTheDocument()
  })

  it('discloses truncation instead of implying that is all of them', () => {
    render(<AlertFeed data={{
      retrieved: true, days: 7, truncated: true,
      alerts: [alert('signals_missing')],
    }} />)
    expect(screen.getByText(/there are more in this window/i)).toBeInTheDocument()
  })

  it('never renders a judgement about the student', () => {
    // The scope is the feature. If a future kind smuggles an inference in,
    // this is what should stop it.
    render(<AlertFeed data={{
      retrieved: true, days: 7,
      alerts: [alert('session_auto_closed'), alert('signals_missing')],
    }} />)
    const text = document.body.textContent
    for (const word of ['stressed', 'struggling', 'inattentive', 'attention',
      'distracted', 'at risk']) {
      expect(text.toLowerCase()).not.toContain(word)
    }
  })
})

// ─── the cohort panels ─────────────────────────────────────────────────────
//
// Two surfaces over one payload. What matters here is the same set of
// distinctions the rest of this file is about, plus one that is only this
// section's: the per-student rows are withheld by the *backend* below its
// floor, so these tests pin that the panel explains the absence rather than
// rendering a blank where a table should be.

describe('ClassSignalTrend', () => {
  const day = (d, over) => ({
    day: d, channel: 'cognitive', avg_focus: 0.6, avg_stress: 0.3,
    avg_engagement: 0.5, sample_count: 100, trusted_sample_count: 100,
    student_count: 3, ...over,
  })
  const heart = (d, bpm) => ({
    day: d, channel: 'heart', avg_heart_rate_bpm: bpm, avg_rmssd_ms: 40,
    sample_count: 50, trusted_sample_count: 50, student_count: 2,
  })

  it('distinguishes a failed read from a class that recorded nothing', () => {
    const { rerender } = render(
      <ClassSignalTrend data={{ retrieved: false, series: [] }} />)
    expect(screen.getByText(/couldn't load the class signal trend/i)).toBeInTheDocument()

    rerender(<ClassSignalTrend data={{ retrieved: true, series: [], days: 30 }} />)
    expect(screen.getByText(/no signals recorded/i)).toBeInTheDocument()
  })

  it('describes the ratio series as percentages, not as 0 to 1', () => {
    // The scale lives in the column spec, and getting it wrong is invisible on
    // screen -- the chart looks right while the text alternative announces a
    // session ranging 42-78% as "Focus 0% to 1%".
    render(<ClassSignalTrend data={{
      retrieved: true, days: 30, timezone: 'UTC',
      series: [day('2026-06-10'), day('2026-06-11', { avg_focus: 0.78 })],
    }} />)
    const summary = screen.getByRole('img').getAttribute('aria-label')
    expect(summary).toMatch(/Focus 60% to 78%/)
    expect(summary).not.toMatch(/Focus 0% to 1%/)
  })

  it('gives the table no heart column when no line is drawn for one', () => {
    // Asserted on the *table*, not the summary sentence, because the sentence
    // omits an empty series on its own -- `describeSeries` returns null when
    // nothing was recorded. So a test reading the aria-label passes whether or
    // not the column is conditional, which makes it inert against exactly the
    // bug it names.
    //
    // The table is where it shows: an unconditional column renders a
    // "not recorded" cell on every row, so a class with no headband announces
    // a heart rate it never had. That is the defect CLAUDE.md records shipping
    // twice, and this is the assertion that can see it.
    render(<ClassSignalTrend data={{
      retrieved: true, days: 30, series: [day('2026-06-10')],
    }} />)
    expect(screen.queryByRole('columnheader', { name: /heart rate/i }))
      .not.toBeInTheDocument()
    expect(screen.queryByText(/not recorded/i)).not.toBeInTheDocument()
  })

  it('describes the heart series when there is one, in bpm rather than percent', () => {
    render(<ClassSignalTrend data={{
      retrieved: true, days: 30,
      series: [day('2026-06-10'), heart('2026-06-10', 72)],
    }} />)
    const summary = screen.getByRole('img').getAttribute('aria-label')
    expect(summary).toMatch(/Heart rate 72 bpm/)
  })

  it('folds the channels of one day into one row rather than one row each', () => {
    render(<ClassSignalTrend data={{
      retrieved: true, days: 30,
      series: [day('2026-06-10'), heart('2026-06-10', 72)],
    }} />)
    // One day, so both series report a single value rather than a range.
    const summary = screen.getByRole('img').getAttribute('aria-label')
    expect(summary).toMatch(/1 day with recordings/)
  })

  it('hides every series when the teacher has hidden sensor data, not just heart', () => {
    // Focus/stress/engagement are EEG-derived and are sensor data too. An
    // earlier version gated only the heart line, so the cognitive lines went on
    // drawing real values underneath a note saying sensor data was hidden.
    //
    // The first version of this test asserted on that note, which is rendered
    // either way -- so it passed against the bug it was written for. Assert on
    // the data instead: no chart, no sr-only table, no numbers.
    render(<ClassSignalTrend hideSensors data={{
      retrieved: true, days: 30,
      series: [day('2026-06-10'), heart('2026-06-10', 72)],
    }} />)
    expect(screen.queryByRole('img')).not.toBeInTheDocument()
    expect(screen.queryByRole('table')).not.toBeInTheDocument()
    expect(document.body.textContent).not.toMatch(/\d+%/)
    expect(document.body.textContent).not.toMatch(/bpm/i)
    expect(screen.getByText(/turn off "hide sensor data"/i)).toBeInTheDocument()
  })
})

describe('ClassSignalRoster', () => {
  const student = (id, over) => ({
    student_id: id, display_name: id.toUpperCase(),
    summary: {
      focus: 0.6, stress: 0.3, heart_rate_bpm: 70, days_recorded: 4,
      cognitive_samples: 100, heart_samples: 50,
      eeg_enabled: true, heart_included: true, consent_retrieved: true, ...over,
    },
  })

  it('explains a withheld breakdown rather than rendering a blank', () => {
    render(<ClassSignalRoster data={{
      retrieved: true, days: 30, class_size: 3, per_student: null, min_students: 5,
    }} />)
    expect(screen.getByText(/withheld for classes smaller than 5/i)).toBeInTheDocument()
  })

  it('renders a row per student once the rows arrive', () => {
    render(<ClassSignalRoster data={{
      retrieved: true, days: 30, class_size: 5,
      per_student: [student('a'), student('b')],
    }} />)
    expect(screen.getByRole('table')).toBeInTheDocument()
    expect(screen.getByRole('rowheader', { name: /^A/ })).toBeInTheDocument()
  })

  it('says why a figure is missing rather than showing a bare dash', () => {
    // The four-state rule: a declined channel reads as a decision, an
    // unreadable consent read as unknown, and neither as "no data".
    render(<ClassSignalRoster data={{
      retrieved: true, days: 30, class_size: 5,
      per_student: [
        student('a', { focus: null, eeg_enabled: false, eeg_revoked_at: '2026-06-01' }),
        student('b', { heart_rate_bpm: null, heart_included: false }),
        student('c', { focus: null, consent_retrieved: false }),
      ],
    }} />)
    expect(screen.getByText(/off since/i)).toBeInTheDocument()
    expect(screen.getByText(/unavailable/i)).toBeInTheDocument()
  })

  it('separates a channel that recorded nothing from one that is switched off', () => {
    render(<ClassSignalRoster data={{
      retrieved: true, days: 30, class_size: 5,
      per_student: [student('a', {
        focus: null, cognitive_samples: 0, eeg_enabled: true,
      })],
    }} />)
    expect(screen.getByText(/no sensor/i)).toBeInTheDocument()
  })

  it('flags an outlier in words, not by colour alone', () => {
    render(<ClassSignalRoster data={{
      retrieved: true, days: 30, class_size: 5,
      per_student: [
        student('a', { focus: 0.8 }), student('b', { focus: 0.8 }),
        student('c', { focus: 0.8 }), student('d', { focus: 0.2 }),
      ],
    }} />)
    expect(screen.getByText(/unlike the class/i)).toBeInTheDocument()
  })

  it('says a failed per-student read is unknown, never that nothing was recorded', () => {
    // The exact payload the backend produces when the totals RPC fails and the
    // trend's succeeds: rows present, `retrieved: false`, null averages, zero
    // counts. Zero counts are what `offLabel` reads as "No sensor" -- an
    // assertion that the student recorded nothing, when nobody asked.
    //
    // The backend grew this flag to stop exactly that claim one layer in; it is
    // only a fix if a consumer reads it.
    render(<ClassSignalRoster data={{
      retrieved: true, days: 30, class_size: 5, summaries_retrieved: false,
      per_student: [student('a', {
        focus: null, stress: null, heart_rate_bpm: null,
        cognitive_samples: 0, heart_samples: 0, days_recorded: 0,
        retrieved: false,
      })],
    }} />)
    expect(screen.queryByText(/no sensor/i)).not.toBeInTheDocument()
    expect(screen.getAllByText(/unavailable/i).length).toBeGreaterThanOrEqual(3)
    // And the day count does not assert zero either.
    expect(screen.queryByRole('cell', { name: '0' })).not.toBeInTheDocument()
  })

  it('still separates a real empty channel from an unread one', () => {
    // Teeth for the test above: with `retrieved: true` the same zero counts are
    // a genuine finding and must keep saying so, or the fix would have replaced
    // one wrong label with another.
    render(<ClassSignalRoster data={{
      retrieved: true, days: 30, class_size: 5,
      per_student: [student('a', {
        focus: null, stress: null, heart_rate_bpm: null,
        cognitive_samples: 0, heart_samples: 0, days_recorded: 0,
        retrieved: true,
      })],
    }} />)
    expect(screen.getAllByText(/no sensor/i).length).toBeGreaterThan(0)
    expect(screen.queryByText(/unavailable/i)).not.toBeInTheDocument()
  })

  it('keeps a known revocation ahead of an unread row', () => {
    // The compound case the two tests above miss by covering `retrieved` false
    // and true separately. Consent comes from a different query than the
    // averages, so when the totals read fails the revocation and its date are
    // still fully known -- and "Unavailable" both discards that and implies a
    // retry might produce a number, when for a revoked channel none can ever
    // appear.
    render(<ClassSignalRoster data={{
      retrieved: true, days: 30, class_size: 5, summaries_retrieved: false,
      per_student: [student('a', {
        focus: null, stress: null, heart_rate_bpm: null,
        cognitive_samples: 0, heart_samples: 0, days_recorded: 0,
        retrieved: false,
        eeg_enabled: false, eeg_revoked_at: '2026-06-03T00:00:00Z',
        heart_included: false, heart_revoked_at: '2026-06-03T00:00:00Z',
      })],
    }} />)
    // Every signal cell knows why it is empty, and none of them blames the outage.
    expect(screen.getAllByText(/off since/i).length).toBe(3)
    expect(screen.queryByText(/unavailable/i)).not.toBeInTheDocument()
  })

  it('still reports the outage for a channel that is on', () => {
    // Teeth for the precedence: the revocation wins only where there is one.
    // A consented channel on an unread row is genuinely unknown.
    render(<ClassSignalRoster data={{
      retrieved: true, days: 30, class_size: 5, summaries_retrieved: false,
      per_student: [student('a', {
        // An unread row carries no averages at all -- the backend nulls every
        // one of them, so leaving a default in place would test a payload it
        // never sends.
        focus: null, stress: null, heart_rate_bpm: null,
        cognitive_samples: 0, heart_samples: 0, days_recorded: 0,
        retrieved: false,
        eeg_enabled: true,
        heart_included: false, heart_revoked_at: '2026-06-03T00:00:00Z',
      })],
    }} />)
    // EEG is on and unread; heart is off and known.
    expect(screen.getAllByText(/unavailable/i).length).toBe(2)
    expect(screen.getAllByText(/off since/i).length).toBe(1)
  })

  it('hides the table when the teacher has hidden sensor data', () => {
    render(<ClassSignalRoster hideSensors data={{
      retrieved: true, days: 30, class_size: 5, per_student: [student('a')],
    }} />)
    expect(screen.queryByRole('table')).not.toBeInTheDocument()
    expect(screen.getByText(/turn off "hide sensor data"/i)).toBeInTheDocument()
  })
})
