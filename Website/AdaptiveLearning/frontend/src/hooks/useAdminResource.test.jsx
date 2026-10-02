import { act, renderHook, waitFor } from '@testing-library/react'
import { it, expect } from 'vitest'
import useAdminResource from './useAdminResource'

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
