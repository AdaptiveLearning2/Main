import { render, screen, within } from '@testing-library/react'
import MeasureTile from './MeasureTile'
import { CHANNEL_REASONS, CHANNEL_LABELS } from '../../test/fixtures/signalSummary'

const ON = CHANNEL_REASONS.noSensor
const VISIBLE = { ignore: 'script, style, .sr-only, .sr-only *, [hidden], [hidden] *' }

function tile(name) {
  return within(screen.getByRole('group', { name }))
}

describe('MeasureTile', () => {
  it('shows the value, the verdict and the usual range inside its own group', () => {
    render(<MeasureTile measure="focus" value="72%" reason={ON}
                        usual={{ status: 'compared', verdict: 'higher', low: 0.55, high: 0.65 }} />)
    const t = tile('Focus')
    expect(t.getByText('72%', VISIBLE)).toBeInTheDocument()
    expect(t.getByText('Higher than usual')).toBeInTheDocument()
    expect(t.getByText('Usual 55–65%')).toBeInTheDocument()
  })

  it('names the measure from the glossary, never "Stress"', () => {
    render(<MeasureTile measure="calm" value="69%" reason={ON} />)
    expect(screen.getByRole('group', { name: 'Calm' })).toBeInTheDocument()
    expect(screen.queryByText(/stress/i, VISIBLE)).toBeNull()
  })

  it('draws no comparison for a payload without `usual`', () => {
    render(<MeasureTile measure="focus" value="72%" reason={ON} />)
    expect(tile('Focus').queryByText(/usual/i)).toBeNull()
  })

  it('says why it was not compared', () => {
    render(<MeasureTile measure="calm" value="69%" reason={ON}
                        usual={{ status: 'not_enough_history', low: null, high: null }} />)
    expect(tile('Calm').getByText('Not enough history yet')).toBeInTheDocument()
    expect(tile('Calm').queryByText(/^Usual/)).toBeNull()
  })

  it.each(Object.keys(CHANNEL_REASONS))('a missing value shows the %s reason, never N/A', (state) => {
    render(<MeasureTile measure="focus" value="N/A" reason={CHANNEL_REASONS[state]} />)
    expect(tile('Focus').getByText(new RegExp(`^${CHANNEL_LABELS[state]}`), VISIBLE)).toBeInTheDocument()
    expect(tile('Focus').queryByText('N/A')).toBeNull()
  })

  it('takes a plain reason when the channel has its own words', () => {
    render(<MeasureTile measure="body_arousal" value={null} reason="Lesson in progress" />)
    expect(tile('Body arousal (heart rate)').getByText('Lesson in progress', VISIBLE))
      .toBeInTheDocument()
  })

  it('carries the explanation and the caveat in its info tip', () => {
    render(<MeasureTile measure="body_arousal" value="22%" reason={ON} />)
    const panel = tile('Body arousal (heart rate)').getByText(/start of each lesson/)
    expect(panel).toHaveTextContent(/excitement, effort and movement/)
  })
})
