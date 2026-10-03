import { act, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { WAKE_AFTER_MS, _resetForTests, startRequest } from '../../lib/serverWake'
import ServerWaking from './ServerWaking'

beforeEach(() => { vi.useFakeTimers(); _resetForTests() })
afterEach(() => { vi.useRealTimers() })

it('says the server is starting while a request waits past the threshold, then goes', () => {
  render(<ServerWaking />)
  expect(screen.queryByRole('status')).toBeNull()

  let done
  act(() => { done = startRequest() })
  expect(screen.queryByRole('status')).toBeNull()
  act(() => { vi.advanceTimersByTime(WAKE_AFTER_MS) })
  expect(screen.getByRole('status')).toHaveTextContent(/Starting the server/)

  act(() => done(true))
  expect(screen.queryByRole('status')).toBeNull()
})
