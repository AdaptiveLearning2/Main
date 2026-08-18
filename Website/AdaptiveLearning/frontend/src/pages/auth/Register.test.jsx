import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'

vi.mock('../../context/AuthContext', () => ({
  useAuth: () => ({ signUp: vi.fn().mockResolvedValue({}) }),
}))
vi.mock('sonner', () => ({ toast: { error: vi.fn(), success: vi.fn() } }))

import Register from './Register'

function draw() {
  render(<MemoryRouter><Register /></MemoryRouter>)
  return screen.getByPlaceholderText(/min 6 characters/i)
}

describe('the password strength meter', () => {
  it('calls the weakest password weak, rather than saying nothing', async () => {
    // Four checks, so a non-empty password can satisfy none of them. The
    // colour and label arrays held '' at index 0, so this drew a grey bar and
    // an empty label -- the one password most in need of the warning was the
    // only one that got none.
    await userEvent.type(draw(), 'abc')

    expect(screen.getByText('Weak')).toBeInTheDocument()
  })

  it('still grades a password that passes every check', async () => {
    // The other end, so the fix above cannot be "always say Weak".
    await userEvent.type(draw(), 'Longenough1!')

    expect(screen.getByText('Strong')).toBeInTheDocument()
  })

  it('says nothing at all before anything is typed', async () => {
    draw()
    expect(screen.queryByText(/^(Weak|Fair|Good|Strong)$/)).not.toBeInTheDocument()
  })
})

describe('the role picker', () => {
  // Matched on each role's own sub-label rather than its title: the submit
  // button reads "Create Student Account", so /student/ finds two things and
  // the failure looks like the attribute being wrong rather than the query
  // being loose.
  const STUDENT = { name: /practice & learn/i }
  const TEACHER = { name: /teach & analyze/i }

  it('reports which role is selected, not just colours it', async () => {
    // Selection was conveyed by border and background alone, so a screen
    // reader had no way to tell which of the three was chosen.
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
