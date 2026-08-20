import { render, screen, cleanup, act } from '@testing-library/react'
import { vi } from 'vitest'
import FlowDot from './FlowDot'

// The pulse fires on a changed timestamp, not on every poll, so a stopped
// sensor goes still instead of blinking as if it were healthy.

const ping = () => document.querySelector('.animate-ping')

const flowing = (ts) => ({ flowing: true, stale: false, seen: true, last_ts: ts })

beforeEach(() => { vi.useFakeTimers() })
afterEach(() => { cleanup(); vi.useRealTimers() })

// Wrap timer advances in act(), or the state update from the timeout lands
// outside React's knowledge and the assertion reads a stale tree.
const advance = (ms) => act(() => { vi.advanceTimersByTime(ms) })

describe('the pulse', () => {
  it('does not fire on the first render, however live the channel is', () => {
    render(<FlowDot channel={flowing('2026-08-18T10:00:00Z')} label="EEG" />)
    expect(ping()).toBeNull()
  })

  it('fires when a new timestamp arrives', () => {
    const { rerender } = render(<FlowDot channel={flowing('2026-08-18T10:00:00Z')} label="EEG" />)
    rerender(<FlowDot channel={flowing('2026-08-18T10:00:01Z')} label="EEG" />)
    expect(ping()).not.toBeNull()
  })

  it('stops 600ms later', () => {
    const { rerender } = render(<FlowDot channel={flowing('2026-08-18T10:00:00Z')} label="EEG" />)
    rerender(<FlowDot channel={flowing('2026-08-18T10:00:01Z')} label="EEG" />)
    advance(599)
    expect(ping()).not.toBeNull()
    advance(1)
    expect(ping()).toBeNull()
  })

  it('does not fire when the same timestamp is polled again', () => {
    const { rerender } = render(<FlowDot channel={flowing('2026-08-18T10:00:00Z')} label="EEG" />)
    rerender(<FlowDot channel={flowing('2026-08-18T10:00:01Z')} label="EEG" />)
    advance(600)

    rerender(<FlowDot channel={flowing('2026-08-18T10:00:01Z')} label="EEG" />)
    rerender(<FlowDot channel={flowing('2026-08-18T10:00:01Z')} label="EEG" />)
    expect(ping()).toBeNull()
  })

  it('restarts the 600ms when a timestamp arrives mid-pulse', () => {
    // A pulse that inherited the previous one's remainder would cut short and
    // make a busy channel look intermittent.
    const { rerender } = render(<FlowDot channel={flowing('2026-08-18T10:00:00Z')} label="EEG" />)
    rerender(<FlowDot channel={flowing('2026-08-18T10:00:01Z')} label="EEG" />)
    advance(500)

    rerender(<FlowDot channel={flowing('2026-08-18T10:00:02Z')} label="EEG" />)
    advance(500)   // 1000ms since the first sample, 500ms since the second
    expect(ping()).not.toBeNull()
    advance(100)
    expect(ping()).toBeNull()
  })

  it('is not drawn for a channel that is no longer flowing', () => {
    const { rerender } = render(<FlowDot channel={flowing('2026-08-18T10:00:00Z')} label="EEG" />)
    rerender(
      <FlowDot
        channel={{ flowing: false, stale: true, seen: true, last_ts: '2026-08-18T10:00:01Z' }}
        label="EEG"
      />)
    expect(ping()).toBeNull()
  })
})

describe('the four states', () => {
  // Check the title text, not the color, since "never reported" and "stale"
  // are different facts that a color alone can't distinguish.
  const titleOf = () => screen.getByTitle(/EEG:/).getAttribute('title')

  it('reports a channel that has never reported', () => {
    render(<FlowDot channel={{ flowing: false, stale: false, seen: false, last_ts: null }} label="EEG" />)
    expect(titleOf()).toMatch(/no data has ever arrived/)
  })

  it('reports a channel receiving data', () => {
    render(<FlowDot channel={flowing('2026-08-18T10:00:00Z')} label="EEG" />)
    expect(titleOf()).toMatch(/receiving data/)
  })

  it('reports a stale channel by how long it has been quiet', () => {
    render(<FlowDot channel={{ flowing: false, stale: true, seen: true, last_ts: 'x' }} label="EEG" />)
    expect(titleOf()).toMatch(/over 10 minutes/)
  })

  it('reports a channel seen but neither flowing nor yet stale', () => {
    render(<FlowDot channel={{ flowing: false, stale: false, seen: true, last_ts: 'x' }} label="EEG" />)
    expect(titleOf()).toMatch(/over 90 seconds ago/)
  })

  it('survives a channel that is missing entirely', () => {
    // `class_live` omits a channel it couldn't read; this must not crash.
    render(<FlowDot channel={undefined} label="EEG" />)
    expect(titleOf()).toMatch(/no data has ever arrived/)
  })
})
