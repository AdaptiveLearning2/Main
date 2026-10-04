import { renderHook } from '@testing-library/react'
import { it, expect, vi, beforeEach, afterEach } from 'vitest'
import usePoll from './usePoll'

// A pure hook with no React state, so fake timers are safe here (unlike the pages).
let hidden = false
beforeEach(() => {
  vi.useFakeTimers()
  hidden = false
  Object.defineProperty(document, 'hidden', { configurable: true, get: () => hidden })
})
afterEach(() => {
  vi.useRealTimers()
  delete document.hidden
})

function setHidden(value) {
  hidden = value
  document.dispatchEvent(new Event('visibilitychange'))
}

function deferred() {
  let resolve, reject
  const promise = new Promise((res, rej) => { resolve = res; reject = rej })
  return { promise, resolve, reject }
}

it('calls at once, then once per interval after each call settles', async () => {
  const fn = vi.fn(async () => {})
  renderHook(() => usePoll(fn, { intervalMs: 1000 }))
  expect(fn).toHaveBeenCalledTimes(1)
  await vi.advanceTimersByTimeAsync(3000)
  expect(fn).toHaveBeenCalledTimes(4)
})

it('waits one interval before the first call when not immediate', async () => {
  const fn = vi.fn(async () => {})
  renderHook(() => usePoll(fn, { intervalMs: 1000, immediate: false }))
  expect(fn).not.toHaveBeenCalled()
  await vi.advanceTimersByTimeAsync(999)
  expect(fn).not.toHaveBeenCalled()
  await vi.advanceTimersByTimeAsync(1)
  expect(fn).toHaveBeenCalledTimes(1)
  await vi.advanceTimersByTimeAsync(1000)
  expect(fn).toHaveBeenCalledTimes(2)
})

it('calls on return when its first wait ended while hidden', async () => {
  const fn = vi.fn(async () => {})
  renderHook(() => usePoll(fn, { intervalMs: 1000, immediate: false }))
  setHidden(true)
  await vi.advanceTimersByTimeAsync(5000)
  expect(fn).not.toHaveBeenCalled()
  setHidden(false)
  await vi.advanceTimersByTimeAsync(0)
  expect(fn).toHaveBeenCalledTimes(1)
})

it('never overlaps a slow call', async () => {
  const slow = deferred()
  const fn = vi.fn(() => slow.promise)
  renderHook(() => usePoll(fn, { intervalMs: 1000 }))
  await vi.advanceTimersByTimeAsync(10_000)
  expect(fn).toHaveBeenCalledTimes(1)
  slow.resolve()
  await vi.advanceTimersByTimeAsync(1000)
  expect(fn).toHaveBeenCalledTimes(2)
})

it('becoming visible during a call does not start a second chain', async () => {
  let pending = deferred()
  const fn = vi.fn(() => pending.promise)
  renderHook(() => usePoll(fn, { intervalMs: 1000 }))
  setHidden(true)
  setHidden(false)
  expect(fn).toHaveBeenCalledTimes(1)
  pending.resolve()
  pending = { promise: Promise.resolve() }
  // One chain: one call per interval from here, not two.
  await vi.advanceTimersByTimeAsync(3000)
  expect(fn).toHaveBeenCalledTimes(4)
})

it('pauses while hidden and calls at once on a return past its due time', async () => {
  const fn = vi.fn(async () => {})
  renderHook(() => usePoll(fn, { intervalMs: 1000 }))
  setHidden(true)
  await vi.advanceTimersByTimeAsync(10_000)
  // The one already scheduled finds the tab hidden and does not call.
  expect(fn).toHaveBeenCalledTimes(1)
  setHidden(false)
  await vi.advanceTimersByTimeAsync(0)
  expect(fn).toHaveBeenCalledTimes(2)
})

it('on return, keeps a wait that was already set', async () => {
  const fn = vi.fn(async () => {})
  renderHook(() => usePoll(fn, { intervalMs: 1000 }))
  await vi.advanceTimersByTimeAsync(0)       // settles at 0; the next call is due at 1000
  setHidden(true)
  await vi.advanceTimersByTimeAsync(400)
  setHidden(false)
  await vi.advanceTimersByTimeAsync(0)
  expect(fn).toHaveBeenCalledTimes(1)
  await vi.advanceTimersByTimeAsync(600)
  expect(fn).toHaveBeenCalledTimes(2)
})

it('on return, waits out the rest of the interval since the last call settled', async () => {
  let pending = deferred()
  const fn = vi.fn(() => pending.promise)
  renderHook(() => usePoll(fn, { intervalMs: 1000 }))
  setHidden(true)
  // Settles while hidden, so no wait is set: the return has to work out what is left.
  pending.resolve()
  pending = { promise: Promise.resolve() }
  await vi.advanceTimersByTimeAsync(400)
  setHidden(false)
  await vi.advanceTimersByTimeAsync(0)
  expect(fn).toHaveBeenCalledTimes(1)
  await vi.advanceTimersByTimeAsync(599)
  expect(fn).toHaveBeenCalledTimes(1)
  await vi.advanceTimersByTimeAsync(1)
  expect(fn).toHaveBeenCalledTimes(2)
})

it('on return, keeps the backed-off wait after a failure', async () => {
  let pending = deferred()
  const fn = vi.fn(() => pending.promise)
  renderHook(() => usePoll(fn, { intervalMs: 1000, maxBackoffMs: 8000 }))
  setHidden(true)
  pending.reject(new Error('down'))           // the next wait doubles to 2000
  pending = { promise: Promise.resolve() }
  await vi.advanceTimersByTimeAsync(1500)
  setHidden(false)
  await vi.advanceTimersByTimeAsync(0)
  expect(fn).toHaveBeenCalledTimes(1)
  await vi.advanceTimersByTimeAsync(499)
  expect(fn).toHaveBeenCalledTimes(1)
  await vi.advanceTimersByTimeAsync(1)
  expect(fn).toHaveBeenCalledTimes(2)
})

it('keeps polling while hidden when told to', async () => {
  const fn = vi.fn(async () => {})
  renderHook(() => usePoll(fn, { intervalMs: 1000, pauseWhenHidden: false }))
  setHidden(true)
  await vi.advanceTimersByTimeAsync(3000)
  expect(fn).toHaveBeenCalledTimes(4)
})

it('doubles the wait after a failure up to the cap, and resets on success', async () => {
  let fail = true
  const fn = vi.fn(async () => { if (fail) throw new Error('down') })
  renderHook(() => usePoll(fn, { intervalMs: 1000, maxBackoffMs: 4000 }))
  await vi.advanceTimersByTimeAsync(1999)
  expect(fn).toHaveBeenCalledTimes(1)        // waiting 2000, not 1000
  await vi.advanceTimersByTimeAsync(1)
  expect(fn).toHaveBeenCalledTimes(2)
  await vi.advanceTimersByTimeAsync(4000)
  expect(fn).toHaveBeenCalledTimes(3)        // 4000: the cap
  await vi.advanceTimersByTimeAsync(4000)
  expect(fn).toHaveBeenCalledTimes(4)        // still 4000
  fail = false
  await vi.advanceTimersByTimeAsync(4000)
  expect(fn).toHaveBeenCalledTimes(5)        // this one succeeds
  await vi.advanceTimersByTimeAsync(1000)
  expect(fn).toHaveBeenCalledTimes(6)        // back to the interval
})

it('applies a new interval at the next wait, without an extra call', async () => {
  const fn = vi.fn(async () => {})
  const { rerender } = renderHook(({ ms }) => usePoll(fn, { intervalMs: ms }),
                                  { initialProps: { ms: 1000 } })
  await vi.advanceTimersByTimeAsync(0)       // the first call settles and schedules 1000
  rerender({ ms: 5000 })
  expect(fn).toHaveBeenCalledTimes(1)
  await vi.advanceTimersByTimeAsync(1000)
  expect(fn).toHaveBeenCalledTimes(2)        // the wait already scheduled
  await vi.advanceTimersByTimeAsync(4999)
  expect(fn).toHaveBeenCalledTimes(2)
  await vi.advanceTimersByTimeAsync(1)
  expect(fn).toHaveBeenCalledTimes(3)
})

it('restarts at once on a new key, and tells the superseded call it is stale', async () => {
  const first = deferred()
  const seen = []
  const fn = vi.fn(async (stopped) => {
    if (fn.mock.calls.length === 1) await first.promise
    seen.push(stopped())
  })
  const { rerender } = renderHook(({ k }) => usePoll(fn, { intervalMs: 1000, key: k }),
                                  { initialProps: { k: 'a' } })
  rerender({ k: 'b' })
  expect(fn).toHaveBeenCalledTimes(2)
  first.resolve()
  await vi.advanceTimersByTimeAsync(0)
  expect(seen).toEqual([false, true])
})

it('stops on unmount', async () => {
  const fn = vi.fn(async () => {})
  const { unmount } = renderHook(() => usePoll(fn, { intervalMs: 1000 }))
  unmount()
  await vi.advanceTimersByTimeAsync(5000)
  expect(fn).toHaveBeenCalledTimes(1)
})
