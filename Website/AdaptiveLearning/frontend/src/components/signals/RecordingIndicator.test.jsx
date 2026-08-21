import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import RecordingIndicator from './RecordingIndicator'

describe('RecordingIndicator', () => {
  it('renders nothing when nothing is recording', () => {
    // The default state for a student with no headband and no camera.
    const { container } = render(<RecordingIndicator channels={[]} />)
    expect(container).toBeEmptyDOMElement()
  })

  it('renders nothing when the caller has not decided yet', () => {
    const { container } = render(<RecordingIndicator channels={undefined} />)
    expect(container).toBeEmptyDOMElement()
  })

  it('names the channels and nothing else', () => {
    // No values, no status colour, no route to the data -- states *that*
    // recording is happening, not *what* was recorded.
    render(<RecordingIndicator channels={['Headband', 'Camera']} />)

    expect(screen.getByRole('status')).toHaveTextContent('Recording: Headband · Camera')
  })

  it('is not interactive', () => {
    render(<RecordingIndicator channels={['Headband']} />)

    expect(screen.queryByRole('button')).not.toBeInTheDocument()
    expect(screen.queryByRole('link')).not.toBeInTheDocument()
  })

  it('does not render a reading', () => {
    // Guards against a future edit adding "72 bpm" or a focus percentage
    // here just because the data is to hand.
    render(<RecordingIndicator channels={['Heart sensor']} />)

    expect(screen.getByRole('status').textContent).not.toMatch(/\d/)
  })
})
