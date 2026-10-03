import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { prefetchWhenIdle } from './prefetch'

beforeEach(() => { vi.useFakeTimers() })
afterEach(() => { vi.useRealTimers(); delete window.requestIdleCallback; delete window.cancelIdleCallback })

it('waits for idle time, and swallows a failed load', async () => {
  let idle
  window.requestIdleCallback = vi.fn(cb => { idle = cb; return 7 })
  const loader = vi.fn(() => Promise.reject(new Error('offline')))
  prefetchWhenIdle(loader)
  expect(loader).not.toHaveBeenCalled()
  idle()
  expect(loader).toHaveBeenCalledTimes(1)
  await Promise.resolve()                      // the rejection is handled, not thrown
})

it('falls back to a short delay where the browser has no idle callback', () => {
  const loader = vi.fn(() => Promise.resolve())
  prefetchWhenIdle(loader)
  vi.advanceTimersByTime(1999)
  expect(loader).not.toHaveBeenCalled()
  vi.advanceTimersByTime(1)
  expect(loader).toHaveBeenCalledTimes(1)
})

it('cancels, so a page left quickly loads nothing', () => {
  const loader = vi.fn(() => Promise.resolve())
  prefetchWhenIdle(loader)()
  vi.advanceTimersByTime(5000)
  expect(loader).not.toHaveBeenCalled()
})
