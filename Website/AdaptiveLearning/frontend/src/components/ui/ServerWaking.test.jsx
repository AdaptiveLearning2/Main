import { act, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { WAKE_AFTER_MS, _resetForTests, startRequest } from '../../lib/serverWake'
import ServerWaking from './ServerWaking'

beforeEach(() => { vi.useFakeTimers(); _resetForTests() })
afterEach(() => { vi.useRealTimers() })

it('says a request is taking longer than usual while it waits past the threshold, then goes', () => {
  render(<ServerWaking />)
  expect(screen.queryByRole('status')).toBeNull()

  let done
  act(() => { done = startRequest() })
  expect(screen.queryByRole('status')).toBeNull()
  act(() => { vi.advanceTimersByTime(WAKE_AFTER_MS) })
  expect(screen.getByRole('status')).toHaveTextContent(/taking longer than usual/)

  act(() => done(true))
  expect(screen.queryByRole('status')).toBeNull()
})

it('names no cause for the wait, since a slow or hung server looks the same as a sleeping one', () => {
  render(<ServerWaking />)
  act(() => { startRequest() })
  act(() => { vi.advanceTimersByTime(WAKE_AFTER_MS) })
  expect(screen.getByRole('status')).not.toHaveTextContent(/start|wak|sleep|asleep|boot|quiet spell|minute/i)
})
