import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'

const signUp = vi.fn().mockResolvedValue({})
vi.mock('../../context/AuthContext', () => ({
  useAuth: () => ({ signUp: (...a) => signUp(...a) }),
}))
vi.mock('sonner', () => ({ toast: { error: vi.fn(), success: vi.fn() } }))

import { AuthApiError } from '@supabase/supabase-js'
import { toast } from 'sonner'
import Register from './Register'

function draw() {
  render(<MemoryRouter><Register /></MemoryRouter>)
  return screen.getByPlaceholderText(/min 6 characters/i)
}

describe('the password strength meter', () => {
  it('calls the weakest password weak, rather than saying nothing', async () => {
    await userEvent.type(draw(), 'abc')

    expect(screen.getByText('Weak')).toBeInTheDocument()
  })

  it('still grades a password that passes every check', async () => {
    await userEvent.type(draw(), 'Longenough1!')

    expect(screen.getByText('Strong')).toBeInTheDocument()
  })

  it('says nothing at all before anything is typed', async () => {
    draw()
    expect(screen.queryByText(/^(Weak|Fair|Good|Strong)$/)).not.toBeInTheDocument()
  })
})

describe('the role picker', () => {
  // The sub-label: /student/ also matches the submit button.
  const STUDENT = { name: /practice & learn/i }
  const TEACHER = { name: /teach & analyze/i }

  it('reports which role is selected, not just colours it', async () => {
    draw()

    expect(screen.getByRole('button', STUDENT)).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByRole('button', TEACHER)).toHaveAttribute('aria-pressed', 'false')

    await userEvent.click(screen.getByRole('button', TEACHER))

    expect(screen.getByRole('button', TEACHER)).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByRole('button', STUDENT)).toHaveAttribute('aria-pressed', 'false')
  })
})

describe('the password visibility toggle', () => {
  it('has a name that says what pressing it does', async () => {
    // Icon-only, so without this a screen reader announced it as "button".
    draw()

    await userEvent.click(screen.getByRole('button', { name: /show password/i }))

    expect(screen.getByRole('button', { name: /hide password/i })).toBeInTheDocument()
  })
})

describe('a refused sign-up', () => {
  it('does not repeat Supabase saying the address is already registered', async () => {
    // Its own sentence would reveal whether the address has an account.
    signUp.mockRejectedValueOnce(new AuthApiError('User already registered', 422, 'user_already_exists'))
    toast.error.mockClear()
    await fillIn({ grade: '5th Grade' })

    await userEvent.click(screen.getByRole('button', { name: /create student account/i }))

    expect(toast.error).toHaveBeenCalledTimes(1)
    expect(toast.error.mock.calls[0][0]).toMatch(/couldn't create an account/i)
    expect(toast.error.mock.calls[0][0]).not.toMatch(/already registered/i)
  })
})

/** Fills every field a sign-up needs, picking `grade` when one is given. */
async function fillIn({ grade } = {}) {
  await userEvent.type(draw(), 'Longenough1!')
  await userEvent.type(screen.getByPlaceholderText('you@example.com'), 'ada@example.com')
  await userEvent.type(screen.getByPlaceholderText('••••••••'), 'Longenough1!')
  if (grade) await userEvent.selectOptions(screen.getByLabelText('Grade'), grade)
}

describe('the grade picker', () => {
  beforeEach(() => { signUp.mockClear(); toast.error.mockClear() })

  it('offers the shared grade list, starting on "Grade not set"', async () => {
    const { GRADES } = await import('../../lib/grades')
    draw()
    const options = [...screen.getByLabelText('Grade').options].map(o => o.textContent)
    expect(options).toEqual(['Grade not set', ...GRADES])
    expect(screen.getByLabelText('Grade')).toHaveDisplayValue('Grade not set')
  })

  it('will not sign a student up without a grade', async () => {
    await fillIn()
    await userEvent.click(screen.getByRole('button', { name: /create student account/i }))

    expect(signUp).not.toHaveBeenCalled()
  })

  it("sends the student's grade with the sign-up", async () => {
    await fillIn({ grade: '5th Grade' })
    await userEvent.click(screen.getByRole('button', { name: /create student account/i }))

    expect(signUp).toHaveBeenCalledWith('ada@example.com', 'Longenough1!', 'student', '', '5th Grade')
  })

  it('asks a teacher for no grade, and sends none even if one was picked first', async () => {
    await fillIn({ grade: '5th Grade' })
    await userEvent.click(screen.getByRole('button', { name: /teach & analyze/i }))
    expect(screen.queryByLabelText('Grade')).not.toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: /create teacher account/i }))

    expect(signUp).toHaveBeenCalledWith('ada@example.com', 'Longenough1!', 'teacher', '', '')
  })
})
