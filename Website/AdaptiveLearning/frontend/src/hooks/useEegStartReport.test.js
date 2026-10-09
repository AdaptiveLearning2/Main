/** The push EEG-start report: once per session, backed off on failure, never sent twice by a retry. */
import { it, expect, beforeEach, afterEach, vi } from 'vitest'
import { act, renderHook } from '@testing-library/react'

vi.mock('../lib/session', () => ({ markEegStarted: vi.fn() }))
vi.mock('../lib/sidecar', () => ({ sidecarKitVersion: vi.fn() }))

import { markEegStarted } from '../lib/session'
import { sidecarKitVersion } from '../lib/sidecar'
import useEegStartReport, { EEG_START_RETRY_MS } from './useEegStartReport'

beforeEach(() => {
  vi.useFakeTimers()
  vi.mocked(markEegStarted).mockReset()
  vi.mocked(sidecarKitVersion).mockReset().mockResolvedValue(null)
})
afterEach(() => { vi.useRealTimers() })

const settle = () => act(async () => {})
const advance = (ms) => act(() => vi.advanceTimersByTimeAsync(ms))
const mount = (props) => renderHook(({ active, sessionId }) => useEegStartReport(active, sessionId),
                                    { initialProps: props })

it('reports once per session while active, and not before', async () => {
  markEegStarted.mockResolvedValue(true)
  const { rerender } = mount({ active: false, sessionId: 's1' })
  await settle()
  expect(markEegStarted).not.toHaveBeenCalled()

  rerender({ active: true, sessionId: 's1' })
  await settle()
  rerender({ active: false, sessionId: 's1' })
  rerender({ active: true, sessionId: 's1' })
  await settle()
  expect(markEegStarted.mock.calls).toEqual([['s1', null]])

  rerender({ active: true, sessionId: 's2' })
  await settle()
  expect(markEegStarted.mock.calls).toEqual([['s1', null], ['s2', null]])
})

it('sends the kit version the sidecar names with the report', async () => {
  markEegStarted.mockResolvedValue(true)
  sidecarKitVersion.mockResolvedValue('0.2.3')
  mount({ active: true, sessionId: 's1' })
  await settle()
  expect(markEegStarted.mock.calls).toEqual([['s1', '0.2.3']])
})

it('backs off after a failure, then stops', async () => {
  markEegStarted.mockResolvedValue(false)
  mount({ active: true, sessionId: 's1' })
  await settle()
  for (const [i, wait] of EEG_START_RETRY_MS.entries()) {
    await advance(wait - 1)
    expect(markEegStarted).toHaveBeenCalledTimes(i + 1)
    await advance(1)
    expect(markEegStarted).toHaveBeenCalledTimes(i + 2)
  }
  await advance(10 * 60_000)
  expect(markEegStarted).toHaveBeenCalledTimes(EEG_START_RETRY_MS.length + 1)
})

it('cancels a pending retry when a re-run sends first', async () => {
  // A flapping link re-runs the effect; the retry that was waiting would send a duplicate.
  markEegStarted.mockResolvedValueOnce(false).mockResolvedValueOnce(false).mockResolvedValue(true)
  const { rerender } = mount({ active: true, sessionId: 's1' })
  await settle()                                    // 1st: fails, a retry waits 5 s
  rerender({ active: false, sessionId: 's1' })
  rerender({ active: true, sessionId: 's1' })
  await settle()                                    // 2nd: the flap sends now, and fails
  expect(markEegStarted).toHaveBeenCalledTimes(2)

  await advance(EEG_START_RETRY_MS[0])
  expect(markEegStarted).toHaveBeenCalledTimes(2)  // the first retry was cancelled
  await advance(EEG_START_RETRY_MS[1])
  expect(markEegStarted).toHaveBeenCalledTimes(3)  // the second failure's own retry
})

it('sends nothing after unmount', async () => {
  markEegStarted.mockResolvedValue(false)
  const { unmount } = mount({ active: true, sessionId: 's1' })
  await settle()
  unmount()
  await advance(10 * 60_000)
  expect(markEegStarted).toHaveBeenCalledTimes(1)
})
