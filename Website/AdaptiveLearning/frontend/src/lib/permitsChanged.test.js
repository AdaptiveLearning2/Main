import { it, expect, vi, afterEach } from 'vitest'
import { announcePermitsChanged, onPermitsChanged } from './permitsChanged'

const sleep = (ms) => new Promise(r => setTimeout(r, ms))

afterEach(() => vi.unstubAllGlobals())

it('reaches a page listening in this browser, and stops once it unsubscribes', async () => {
  const heard = vi.fn()
  const unsubscribe = onPermitsChanged(heard)
  announcePermitsChanged()
  await vi.waitFor(() => expect(heard).toHaveBeenCalledTimes(1))
  unsubscribe()
  announcePermitsChanged()
  // Delivery is asynchronous: the first one arrived within this, so a second would have too.
  await sleep(50)
  expect(heard).toHaveBeenCalledTimes(1)
})

it('does nothing where the browser has no BroadcastChannel', () => {
  vi.stubGlobal('BroadcastChannel', undefined)
  const unsubscribe = onPermitsChanged(vi.fn())
  expect(() => announcePermitsChanged()).not.toThrow()
  expect(() => unsubscribe()).not.toThrow()
})
