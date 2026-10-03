import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { AWAKE_FOR_MS, WAKE_AFTER_MS, _resetForTests, serverWaking, startRequest, subscribe } from './serverWake'

// A pure module: fake timers are safe here.
beforeEach(() => { vi.useFakeTimers(); _resetForTests() })
afterEach(() => { vi.useRealTimers() })

it('reads a request pending past the threshold as a sleeping server', () => {
  startRequest()
  vi.advanceTimersByTime(WAKE_AFTER_MS - 1)
  expect(serverWaking()).toBe(false)
  vi.advanceTimersByTime(1)
  expect(serverWaking()).toBe(true)
})

it('stops saying so once any HTTP answer comes back', () => {
  const done = startRequest()
  vi.advanceTimersByTime(WAKE_AFTER_MS)
  done(true)
  expect(serverWaking()).toBe(false)
})

it('keeps saying so after a network error, which is not an answer', () => {
  const failed = startRequest()
  const slow = startRequest()
  vi.advanceTimersByTime(WAKE_AFTER_MS)
  failed(false)
  expect(serverWaking()).toBe(true)
  slow(true)
  expect(serverWaking()).toBe(false)
})

it('does not blame a sleeping server for one slow request soon after an answer', () => {
  startRequest()(true)
  startRequest()
  vi.advanceTimersByTime(WAKE_AFTER_MS)
  expect(serverWaking()).toBe(false)
  // Long after the last answer, the same wait reads as sleep again.
  vi.advanceTimersByTime(AWAKE_FOR_MS)
  expect(serverWaking()).toBe(true)
})

it('tells subscribers when the threshold passes, with no request event to do it', () => {
  const listener = vi.fn()
  const unsubscribe = subscribe(listener)
  startRequest()
  listener.mockClear()
  vi.advanceTimersByTime(WAKE_AFTER_MS)
  expect(listener).toHaveBeenCalled()
  unsubscribe()
})
