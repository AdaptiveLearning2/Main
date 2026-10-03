/** The admin data-flow poll: one read at a time, and none while the tab is hidden. */
import { it, expect, vi, beforeEach, afterEach } from 'vitest'
import { act, render } from '@testing-library/react'

vi.mock('../../lib/api', async () => await import('../../test/mocks/apiFetch'))

import { apiFetch, mockApi, resetApi } from '../../test/mocks/apiFetch'
import AdminLiveFlow from './LiveFlow'

// No Testing Library async queries here, so the fake clock cannot stall a `waitFor`.
const advance = (ms) => act(async () => { await vi.advanceTimersByTimeAsync(ms) })
const reads = () => apiFetch.mock.calls.filter(([p]) => p === '/api/admin/live-signals').length
const EMPTY = { retrieved: true, sessions: [] }

let hidden = false
beforeEach(() => {
  resetApi()
  vi.useFakeTimers()
  hidden = false
  Object.defineProperty(document, 'hidden', { configurable: true, get: () => hidden })
})
afterEach(() => {
  vi.useRealTimers()
  delete document.hidden
})

it('does not start a read while a slow one is in flight', async () => {
  let finish
  mockApi({ '/api/admin/live-signals': () => new Promise(r => { finish = r }) })
  render(<AdminLiveFlow />)
  await advance(30_000)
  expect(reads()).toBe(1)
  mockApi({ '/api/admin/live-signals': () => EMPTY })
  await act(async () => { finish(EMPTY) })
  await advance(5_000)
  expect(reads()).toBe(2)
})

it('pauses its reads while the tab is hidden', async () => {
  mockApi({ '/api/admin/live-signals': () => EMPTY })
  render(<AdminLiveFlow />)
  await advance(0)
  hidden = true
  document.dispatchEvent(new Event('visibilitychange'))
  await advance(30_000)
  expect(reads()).toBe(1)
  hidden = false
  document.dispatchEvent(new Event('visibilitychange'))
  await advance(0)
  expect(reads()).toBe(2)
})
