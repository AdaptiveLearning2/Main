/** The page words a refused sign-in via `lib/authErrors`, never Supabase's message. */
import { it, expect, vi, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'

const signIn = vi.fn()
vi.mock('../../context/AuthContext', () => ({
  useAuth: () => ({ signIn: (...a) => signIn(...a) }),
}))
vi.mock('sonner', () => ({ toast: { error: vi.fn(), success: vi.fn() } }))

import { AuthApiError, AuthRetryableFetchError } from '@supabase/supabase-js'
import { toast } from 'sonner'
import Login from './Login'

beforeEach(() => {
  signIn.mockReset()
  toast.error.mockClear()
})

async function submit() {
  render(<MemoryRouter><Login /></MemoryRouter>)
  await userEvent.type(screen.getByPlaceholderText('you@example.com'), 'ada@example.com')
  await userEvent.type(screen.getByPlaceholderText('••••••••'), 'not-it')
  await userEvent.click(screen.getByRole('button', { name: /sign in/i }))
}

it('says the email or password is wrong, not what Supabase said', async () => {
  signIn.mockRejectedValue(new AuthApiError('Invalid login credentials', 400, 'invalid_credentials'))

  await submit()

  expect(toast.error).toHaveBeenCalledWith('Email or password is incorrect.')
})

it('does not call an unreachable service a wrong password', async () => {
  signIn.mockRejectedValue(new AuthRetryableFetchError('Failed to fetch', 0))

  await submit()

  expect(toast.error.mock.calls[0][0]).toMatch(/couldn't reach/i)
})
