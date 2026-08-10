import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import RecordingIndicator from './RecordingIndicator'

describe('RecordingIndicator', () => {
  it('renders nothing when nothing is recording', () => {
    // The default state for a student with no headband and no camera. A
    // permanent empty badge on their screen is worse than silence.
    const { container } = render(<RecordingIndicator channels={[]} />)
    expect(container).toBeEmptyDOMElement()
  })

  it('renders nothing when the caller has not decided yet', () => {
    const { container } = render(<RecordingIndicator channels={undefined} />)
    expect(container).toBeEmptyDOMElement()
  })

  it('names the channels and nothing else', () => {
    // No values, no status colour, no route to the data. It states *that*
    // recording is happening, not *what* was recorded, and that distinction is
    // what makes it compatible with students seeing no signal data.
    render(<RecordingIndicator channels={['Headband', 'Camera']} />)

    expect(screen.getByRole('status')).toHaveTextContent('Recording: Headband · Camera')
  })

  it('is not interactive', () => {
    render(<RecordingIndicator channels={['Headband']} />)

    expect(screen.queryByRole('button')).not.toBeInTheDocument()
    expect(screen.queryByRole('link')).not.toBeInTheDocument()
  })

  it('does not render a reading', () => {
    // A guard against the obvious future edit: someone adds "72 bpm" or a focus
    // percentage here because the data is to hand.
    render(<RecordingIndicator channels={['Heart sensor']} />)

    expect(screen.getByRole('status').textContent).not.toMatch(/\d/)
  })
})
