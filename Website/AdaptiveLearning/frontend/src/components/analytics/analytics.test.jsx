import { render, screen } from '@testing-library/react'
import { describe, it, expect } from 'vitest'
import Heatmap from '../charts/Heatmap'
import Panel from './Panel'
import ClassTopicHeatmap from './ClassTopicHeatmap'
import ClassAccuracyTrend from './ClassAccuracyTrend'
import ClassTimeOfDay from './ClassTimeOfDay'
import FocusAccuracy from './FocusAccuracy'

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
