import { render, screen, cleanup, act } from '@testing-library/react'
import { vi } from 'vitest'
import FlowDot from './FlowDot'

// The dot's four states are not a scale, and the pulse is the one part of it
// that carries information over time: it fires on a *changed* timestamp, so a
// sensor that stopped goes visibly still instead of blinking at the poll rate.
// A pulse driven by the poll rather than by the data would look identical on a
// screenshot and mean the opposite, which is what these tests pin.

const ping = () => document.querySelector('.animate-ping')

const flowing = (ts) => ({ flowing: true, stale: false, seen: true, last_ts: ts })

beforeEach(() => { vi.useFakeTimers() })
afterEach(() => { cleanup(); vi.useRealTimers() })

// Advancing timers has to be wrapped, or the state update the timeout schedules
// lands outside React's knowledge and the assertion reads a stale tree.
const advance = (ms) => act(() => { vi.advanceTimersByTime(ms) })

describe('the pulse', () => {
  it('does not fire on the first render, however live the channel is', () => {
    // The dot mounts holding whatever timestamp it was given, so a page that
    // renders it every second does not pulse every second on arrival.
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
    // The point of the whole component: a stopped sensor is re-rendered with an
    // unchanged `last_ts` on every poll, and must go still rather than blink.
    const { rerender } = render(<FlowDot channel={flowing('2026-08-18T10:00:00Z')} label="EEG" />)
    rerender(<FlowDot channel={flowing('2026-08-18T10:00:01Z')} label="EEG" />)
    advance(600)

    rerender(<FlowDot channel={flowing('2026-08-18T10:00:01Z')} label="EEG" />)
    rerender(<FlowDot channel={flowing('2026-08-18T10:00:01Z')} label="EEG" />)
    expect(ping()).toBeNull()
  })

  it('does not fire when a timestamp goes missing and comes back unchanged', () => {
    // The backend answers `last_ts: null` for a channel it could not read --
    // `/api/admin/live-signals` has an explicit `seen: null` state for exactly
    // that -- so a poll can go value, null, same-value on a session with
    // nothing new in it.
    //
    // Comparing against the *previous rendered* value calls that a change,
    // twice, and lights the dot on the way back. The comparison has to be
    // against the last timestamp actually pulsed for, which the 600ms timer
    // must not clear.
    const { rerender } = render(<FlowDot channel={flowing('2026-08-18T10:00:00Z')} label="EEG" />)
    rerender(<FlowDot channel={flowing('2026-08-18T10:00:01Z')} label="EEG" />)
    advance(600)
    expect(ping()).toBeNull()

    rerender(<FlowDot channel={flowing(null)} label="EEG" />)
    rerender(<FlowDot channel={flowing('2026-08-18T10:00:01Z')} label="EEG" />)

    expect(ping()).toBeNull()
  })

  it('restarts the 600ms when a timestamp arrives mid-pulse', () => {
    // Rather than inheriting the remainder of the previous one, which would cut
    // the second sample's pulse short and make a busy channel look intermittent.
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
    // A new timestamp on a channel the backend has already called stale is not
    // something to celebrate with a green ping.
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
  // Titles rather than colours: "never reported" is a different fact from
  // "stale", not a worse version of it, and the title is where that is said in
  // words rather than in a shade of a dot.
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
    // `class_live` omits a channel it could not read, and a crash here would
    // take the whole admin flow view down with it.
    render(<FlowDot channel={undefined} label="EEG" />)
    expect(titleOf()).toMatch(/no data has ever arrived/)
  })
})
