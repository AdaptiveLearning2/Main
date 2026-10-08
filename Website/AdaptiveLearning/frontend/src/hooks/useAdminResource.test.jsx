import { act, renderHook, waitFor } from '@testing-library/react'
import { it, expect, vi } from 'vitest'
import useAdminResource from './useAdminResource'

const advance = (ms) => act(async () => { await vi.advanceTimersByTimeAsync(ms) })

it('does not start a re-read while a slow one is in flight', async () => {
  vi.useFakeTimers()
  try {
    let finish
    const slow = vi.fn(() => new Promise(r => { finish = r }))
    renderHook(() => useAdminResource({ load: slow, pollMs: 1000 }))
    await advance(10_000)
    expect(slow).toHaveBeenCalledTimes(1)
    await act(async () => { finish({ v: 1 }) })
    await advance(1000)
    expect(slow).toHaveBeenCalledTimes(2)
  } finally {
    vi.useRealTimers()
  }
})

it('pauses its re-reads while the tab is hidden', async () => {
  vi.useFakeTimers()
  let hidden = false
  Object.defineProperty(document, 'hidden', { configurable: true, get: () => hidden })
  const setHidden = (v) => { hidden = v; document.dispatchEvent(new Event('visibilitychange')) }
  try {
    const read = vi.fn(async () => ({ v: 1 }))
    renderHook(() => useAdminResource({ load: read, pollMs: 1000 }))
    await advance(0)
    setHidden(true)
    await advance(5000)
    expect(read).toHaveBeenCalledTimes(1)
    setHidden(false)
    await advance(0)
    expect(read).toHaveBeenCalledTimes(2)
  } finally {
    vi.useRealTimers()
    delete document.hidden
  }
})

// Stable across renders, as the hook requires of `load`.
const load = () => Promise.resolve({ v: 1 })

async function loaded() {
  const hook = renderHook(() => useAdminResource({ load }))
  await waitFor(() => expect(hook.result.current.data).toEqual({ v: 1 }))
  return hook
}

it('adopts what a successful write returns and frees the controls', async () => {
  const { result } = await loaded()
  let ok
  await act(async () => { ok = await result.current.mutate(() => Promise.resolve({ v: 2 })) })
  expect(ok).toBe(true)
  expect(result.current.data).toEqual({ v: 2 })
  expect(result.current.busy).toBe(false)
})

it('resolves false and frees the controls when a write rejects with no Error at all', async () => {
  // "Never throws": a bare rejection must not escape through `e.message` and leave `busy` set.
  const { result } = await loaded()
  let ok
  await act(async () => { ok = await result.current.mutate(() => Promise.reject(undefined)) })
  expect(ok).toBe(false)
  expect(result.current.busy).toBe(false)
  expect(result.current.data).toEqual({ v: 1 })
})

it('keeps a failed read as an Error with its status, and clears it on the next good read', async () => {
  vi.useFakeTimers()
  try {
    const refused = Object.assign(new Error('nope'), { status: 503 })
    const read = vi.fn()
      .mockResolvedValue({ v: 2 })
      .mockResolvedValueOnce({ v: 1 })
      .mockRejectedValueOnce(refused)
    const { result } = renderHook(() => useAdminResource({ load: read, pollMs: 1000 }))
    await advance(0)
    expect(result.current.data).toEqual({ v: 1 })

    await advance(1000)
    // The last good data stays beside the failure.
    expect(result.current.loadError).toBe(refused)
    expect(result.current.loadError.status).toBe(503)
    expect(result.current.data).toEqual({ v: 1 })

    // usePoll backs off after a throw, so allow for the longer wait.
    await advance(5000)
    expect(result.current.loadError).toBeNull()
    expect(result.current.data).toEqual({ v: 2 })
  } finally {
    vi.useRealTimers()
  }
})
